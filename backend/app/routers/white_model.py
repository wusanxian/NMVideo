"""白模：替换提示词结构 + 本机白模生成能力探测（第二十批）

两个端点，都不写库：

- `GET /api/white-model/sections` —— 九段职责结构 + 职责对照。
  给 UI 展示"这套提示词是由哪九段拼出来的、每份素材不负责什么"。
- `GET /api/white-model/capability` —— 用 ComfyUI `/object_info` 探测本机能不能
  **生成白模**（把一段真人视频变成只留动作与镜头的灰白视频）。

关于能力探测的边界（与项目约定一致）：
- 只走官方 `/object_info`，不读服务端文件系统、不认自定义节点目录。
- 探测失败（ComfyUI 不可达）时**如实报告"无法探测"**，不猜、不阻断。
- 「节点在位」只说明**有可能**跑通，不等于链路已验证——本文件的返回里
  `verified` 恒为 false，除非真的跑过一次出片。不得把它当成功承诺。
"""
from fastapi import APIRouter

from pathlib import Path
import json

from .. import comfyui, white_model
from ..comfyui import ComfyUIClient, ComfyUIError
from ..workflow_capabilities import combo_options

router = APIRouter(prefix="/api/white-model", tags=["white-model"])

# 白模生成用的内置工作流预设 id（与 engines/comfy.py 的 workflow_presets 一致）
WHITEMODEL_PRESET_ID = "nmvideo_whitemodel"

# 两条可选路径的节点依赖。
# 真白模：3D 人体重建 → 素模渲染 → 灰白视频（外观信息在重建时就被丢掉）
# 骨架：关键点提取 → 画骨架（更快，但输出是骨架图而非灰模）
PATH_TRUE_MODEL = "sam3d_body_mesh"
PATH_SKELETON = "sdpose_skeleton"

PATH_NODES = {
    PATH_TRUE_MODEL: [
        "SAM3DBody_Loader", "SAM3DBody_Predict", "SAM3DBody_Render",
    ],
    PATH_SKELETON: [
        "SDPoseKeypointExtractor", "SDPoseDrawKeypoints",
    ],
}

PATH_LABELS = {
    PATH_TRUE_MODEL: "真白模（3D 人体素模渲染）",
    PATH_SKELETON: "骨架视频（关键点提取 + 绘制）",
}

PATH_NOTES = {
    PATH_TRUE_MODEL: (
        "输出灰白无纹理人体，外观信息在重建阶段即被丢弃，最接近「白模」语义；"
        "需要 SAM3DBody 权重在位（LoadImage 的 model_file 枚举非空才算真正可用）。"
    ),
    PATH_SKELETON: (
        "输出骨架线框视频，更轻；但它保留了原视频画面底子，"
        "对「不继承白模灰白外观」这类约束的支撑不如真白模。"
    ),
}


# 白模生成模式额外需要的「视频进出」节点（预设里除了 SAM3DBody_* 还要它们）
WHITEMODEL_VIDEO_NODES = ["LoadVideo", "GetVideoComponents", "CreateVideo", "SaveVideo"]

_NEEDED_CLASSES = sorted({n for v in PATH_NODES.values() for n in v} | set(WHITEMODEL_VIDEO_NODES))


def _probe() -> tuple:
    """探测本机节点与权重。返回 (info, error)。

    只请求关心的那几个节点类名（`object_info` 支持按名过滤），
    避免把 900+ 节点的整包拉回来。
    """
    try:
        client = ComfyUIClient()
        info = client.object_info(_NEEDED_CLASSES)
    except (ComfyUIError, OSError, ValueError) as e:
        return None, f"无法探测 ComfyUI（{type(e).__name__}: {e}）"
    if not isinstance(info, dict):
        return None, "ComfyUI /object_info 返回格式异常"
    return info, ""


def _model_files_in(info: dict, class_name: str, input_name: str) -> list:
    """从 /object_info 里读某个节点某输入的枚举（例如权重清单）。

    枚举为空 = 节点壳空转（与 ControlNet 那次同一个陷阱）。
    形状解析统一走 `workflow_capabilities.combo_options`（四种形状都要认，
    只认一种会把"有权重"误判成"没权重"——实测踩过）。
    """
    node = (info or {}).get(class_name) or {}
    spec = ((node.get("input") or {}).get("required") or {}).get(input_name)
    return combo_options(spec)


def _combo_detail(info: dict, class_name: str, input_name: str) -> tuple:
    """读某输入的 (选项列表, 各选项的嵌套子参数)。

    动态 COMBO（`COMFY_DYNAMICCOMBO_V3`）的每个选项可以自带一组子输入——
    选中它就必须一起提交，否则节点校验直接报缺参。所以「有哪些选项」和
    「哪些选项能单独提交」是两件事，必须分开报，不能只给一个字符串列表。

    这对白模链路很关键：`SAM3DBody_Render.render_style` 就是动态 COMBO，
    `mesh`（真白模）带嵌套子参数，`silhouette` 不带。预设默认值只能选
    后者，前者要等实跑确认子参数形态。

    选项解析复用 combo_options，避免两处各写一套形状判断（必有一处落后）。
    """
    node = (info or {}).get(class_name) or {}
    spec = ((node.get("input") or {}).get("required") or {}).get(input_name)
    if not isinstance(spec, (list, tuple)) or not spec:
        return [], {}

    options = combo_options(spec)
    nested: dict = {}
    if len(spec) > 1 and isinstance(spec[1], dict):
        for item in spec[1].get("options") or []:
            if isinstance(item, dict) and item.get("key") is not None:
                sub = item.get("inputs")
                if isinstance(sub, dict) and sub:
                    nested[str(item["key"])] = sorted(str(k) for k in sub)
    return options, nested


def _preset_status() -> dict:
    """本仓库内置的白模工作流预设是否在位（读的是我们自己的 JSON，不是服务端）。"""
    from ..config import BASE_DIR

    preset_id = WHITEMODEL_PRESET_ID
    path = Path(BASE_DIR) / "comfy_workflows" / "presets" / f"{preset_id}.json"
    if not path.is_file():
        return {"preset": preset_id, "present": False, "mode": "", "error": "预设文件缺失"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"preset": preset_id, "present": False, "mode": "", "error": f"预设读取失败: {e}"}
    meta = data.get("_meta") or {}
    return {
        "preset": preset_id,
        "present": bool((data.get("prompt") or {})),
        "mode": str(meta.get("mode") or ""),
        "verified": bool(meta.get("verified")),
        "unverified_params": list(meta.get("unverified_params") or []),
        "render_style_default": str(meta.get("render_style_default") or ""),
    }


@router.get("/sections")
def sections():
    """九段职责结构 + 三份素材的职责对照（只读）。"""
    return {
        "sections": [{"no": no, "name": name}
                     for no, name in white_model.SECTIONS],
        "duties": white_model.duties(
            white_model.DEFAULT_ACTION_REF,
            white_model.DEFAULT_IDENTITY_REF,
            white_model.DEFAULT_ENVIRONMENT_REF,
            has_identity=True, has_environment=True,
        ),
        "note": "第 02 段「参考素材职责」是整套写法里真正起作用的部分："
                "每份素材必须写明「不采用」什么，只写参考什么模型会连不想要的一起参考。",
    }


@router.get("/capability")
def capability():
    """本机白模生成能力探测。**节点在位 ≠ 链路已验证**。"""
    preset = _preset_status()
    info, err = _probe()
    if info is None:
        return {
            "probe_ok": False,
            "error": err,
            "paths": [],
            "generation": {"mode": "whitemodel", **preset},
            "verified": False,
            "note": "探测失败时不猜结论、也不阻断生成；节点是否齐全需要 ComfyUI 起来后再看。",
        }

    names = set(info.keys())
    missing_video_io = [n for n in WHITEMODEL_VIDEO_NODES if n not in names]
    paths = []
    for key, required in PATH_NODES.items():
        missing = [n for n in required if n not in names]
        entry = {
            "path": key,
            "label": PATH_LABELS[key],
            "note": PATH_NOTES[key],
            "required_nodes": required,
            "missing_nodes": missing,
            "available": not missing,
        }
        if key == PATH_TRUE_MODEL:
            # 节点在还不够：权重枚举为空等于"节点壳空转"（与 ControlNet 那次同一个陷阱）
            files = _model_files_in(info, "SAM3DBody_Loader", "model_file")
            entry["model_files"] = files
            entry["model_ready"] = bool(files)
            if not missing and not files:
                entry["available"] = False
                entry["blocked_by"] = "节点在位但 SAM3DBody 权重为空 —— 无法实际推理"
            # render_style 是动态 COMBO：哪些选项能单独提交，必须分开报。
            styles, nested = _combo_detail(info, "SAM3DBody_Render", "render_style")
            entry["render_style"] = {
                "options": styles,
                "nested_inputs": nested,
                "safe_defaults": [s for s in styles if not nested.get(s)],
                "preset_default": preset.get("render_style_default", ""),
            }
            entry["preset_style_is_safe"] = (
                not styles or not preset.get("render_style_default")
                or not nested.get(preset.get("render_style_default", ""))
            )
        paths.append(entry)

    return {
        "probe_ok": True,
        "probed_classes": _NEEDED_CLASSES,
        "paths": paths,
        "generation": {
            "mode": "whitemodel",
            **preset,
            "video_io_nodes": WHITEMODEL_VIDEO_NODES,
            "missing_video_io_nodes": missing_video_io,
        },
        "any_available": any(p["available"] for p in paths),
        "verified": False,
        "note": "以上只反映节点与权重是否在位；本机**尚未跑通**白模生成链路，"
                "不构成可用承诺。真正的验证需要一次实跑出片。",
    }
