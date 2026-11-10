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

from .. import comfyui, white_model
from ..comfyui import ComfyUIClient, ComfyUIError

router = APIRouter(prefix="/api/white-model", tags=["white-model"])

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


_NEEDED_CLASSES = sorted({n for v in PATH_NODES.values() for n in v})


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
    `/object_info` 里这个形状**不止一种**，三种都要认，否则会把"有权重"误判成"没权重"：

        ["a.safetensors", "b.safetensors"]          # 普通 COMBO
        ["COMBO", {"options": [...]}]               # 带元信息的 COMBO（SAM3DBody_Loader 就是这种）
        ["COMFY_DYNAMICCOMBO_V3", {"options": [{key, inputs}, ...]}]   # 动态组合

    这是实测踩过的坑：只认第一种时，`SAM3DBody_Loader.model_file`
    明明有 `sam_3d_body_dinov3_bf16.safetensors`，却被报成空。
    """
    node = (info or {}).get(class_name) or {}
    spec = ((node.get("input") or {}).get("required") or {}).get(input_name)
    if not isinstance(spec, list) or not spec:
        return []

    def _opts(value) -> list:
        if not isinstance(value, list):
            return []
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and item.get("key") is not None:
                out.append(str(item["key"]))
        return out

    first = spec[0]
    # 形状一：直接是字符串列表
    if isinstance(first, str) and not _looks_like_combo_tag(first):
        return [str(x) for x in spec if isinstance(x, str)]
    # 形状二 / 三：["TAG", {"options": [...]}]
    if isinstance(first, str) and len(spec) > 1 and isinstance(spec[1], dict):
        return _opts(spec[1].get("options") or [])
    # 兜底：整体就是选项列表
    return _opts(spec)


def _looks_like_combo_tag(value: str) -> bool:
    """COMBO / COMFY_DYNAMICCOMBO_V3 这类类型标签（全大写或带下划线+数字）。"""
    return value.isupper() or value.startswith("COMFY_")


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
    info, err = _probe()
    if info is None:
        return {
            "probe_ok": False,
            "error": err,
            "paths": [],
            "verified": False,
            "note": "探测失败时不猜结论、也不阻断生成；节点是否齐全需要 ComfyUI 起来后再看。",
        }

    names = set(info.keys())
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
        paths.append(entry)

    return {
        "probe_ok": True,
        "probed_classes": _NEEDED_CLASSES,
        "paths": paths,
        "any_available": any(p["available"] for p in paths),
        "verified": False,
        "note": "以上只反映节点与权重是否在位；本机**尚未跑通**白模生成链路，"
                "不构成可用承诺。真正的验证需要一次实跑出片。",
    }
