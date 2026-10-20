"""PR-03：工作流能力中心——能力自描述元数据 + ComfyUI 节点探测。

每个工作流模板/预设可在 ``_meta`` 里声明能力（与既有 name/description/
requires/mode 字段并存，只加字段不改旧值）::

    "_meta": {
        "name": "...", "description": "...", "requires": "...",
        "mode": "t2v",
        "execution_kind": "local" | "paid_api",      # 本地免费 / 云端付费 API
        "model_id": "minimax-h3" | "wan2.2-ti2v-5b" | ...
        "required_nodes": ["MiniMaxAPIConfig", ...], # 缺任一节点无法执行
        "required_models": ["wan2.2_ti2v_5B_fp16.safetensors", ...],
                                                     # 缺任一模型无法执行
        "supports": ["t2v", "i2v", "first_last"],    # 支持的输入模式
        "min_vram_gb": 8,                            # 0/缺省 = 不限（API 类）
        "vram_hint": "RTX 4060 8GB 可跑 480p; ...",  # 展示用资源提示
    }

兼容规则（不破坏旧配置）：
- 模板无 ``_meta`` 或字段缺失 → 能力未知，**不做任何阻断**（行为与旧版
  完全一致；默认模板不受影响）
- 缺节点探测依赖 ComfyUI ``/object_info``；探测失败（ComfyUI 不可达）时
  如实报告「无法探测」，生成侧不因探测失败而拦截（后续提交自然失败）
"""
import json
from typing import Optional

SUPPORTED_MODES = ("t2v", "i2v", "first_last", "continuation")

EXECUTION_KINDS = ("local", "paid_api")

_EXECUTION_LABELS = {"local": "本地免费", "paid_api": "付费 API"}


def _norm_str_list(value) -> list:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def capability_from_template(template: dict) -> Optional[dict]:
    """从模板 ``_meta`` 解析规范化能力声明；无 ``_meta`` 返回 None。

    字段缺失时取保守默认：execution_kind=local、required_nodes=[]、
    supports=全部模式（与旧行为一致，不额外阻断）。
    """
    if not isinstance(template, dict):
        return None
    meta = template.get("_meta")
    if not isinstance(meta, dict):
        return None
    kind = str(meta.get("execution_kind") or "local").strip()
    if kind not in EXECUTION_KINDS:
        kind = "local"
    supports = _norm_str_list(meta.get("supports")) or list(SUPPORTED_MODES)
    try:
        min_vram = float(meta.get("min_vram_gb") or 0)
    except (TypeError, ValueError):
        min_vram = 0.0
    return {
        "name": str(meta.get("name") or ""),
        "execution_kind": kind,
        "execution_label": _EXECUTION_LABELS[kind],
        "model_id": str(meta.get("model_id") or ""),
        "required_nodes": _norm_str_list(meta.get("required_nodes")),
        "required_models": _norm_str_list(meta.get("required_models")),
        "supports": [m for m in supports if m in SUPPORTED_MODES] or list(SUPPORTED_MODES),
        "min_vram_gb": min_vram,
        "vram_hint": str(meta.get("vram_hint") or ""),
        "native_preview": bool(meta.get("native_preview")),
        "character_images": bool(meta.get("character_images")),
        "character_lora": bool(meta.get("character_lora")),
    }


def load_all_capabilities() -> list:
    """默认模板 + 全部内置预设的能力列表（供设置页展示）。

    返回项：{id, name, description, requires, mode, capability}
    capability 为 None 表示该模板未声明能力（旧配置，不参与阻断）。
    """
    from .comfyui import PRESETS_DIR, _default_template, load_workflow_template

    items = []

    def _add(pid: str, data: dict, name: str, desc: str, requires: str):
        meta = data.get("_meta") or {}
        items.append({
            "id": pid,
            "name": name,
            "description": desc,
            "requires": requires,
            "mode": meta.get("mode", "t2v"),
            "capability": capability_from_template(data),
        })

    try:
        cur = load_workflow_template()
    except Exception:
        cur = {}
    _add("current", cur, "当前模板（default.json）", "当前生效的工作流模板", "")
    _add("default", _default_template(), "默认 · SD1.5 帧序列",
         "开箱即用：CheckpointLoaderSimple + KSampler + SaveImage（PNG 序列）", "无（vanilla ComfyUI）")
    if PRESETS_DIR.exists():
        for f in sorted(PRESETS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            meta = data.get("_meta") or {}
            _add(f.stem, data, meta.get("name", f.stem),
                 meta.get("description", ""), meta.get("requires", ""))
    return items


def check_missing_nodes(client, required_nodes: list) -> tuple:
    """探测 ComfyUI 实际可用节点，返回 (缺失节点列表, 探测是否成功)。

    - required_nodes 为空 → ([], True)
    - /object_info 不可达 → ([], False)（不阻断，交由提交链路自然失败）
    - 正常 → 缺失的节点名列表
    """
    required = _norm_str_list(required_nodes)
    if not required:
        return [], True
    try:
        info = client.object_info(node_names=required)
    except Exception:
        return [], False
    missing = [n for n in required if n not in (info or {})]
    return missing, True


def _basename(value: str) -> str:
    """模型文件名的规范化 basename（统一斜杠、小写，便于跨平台比较）。"""
    v = str(value).replace("\\", "/")
    return v.rsplit("/", 1)[-1].strip().lower()


def _iter_loader_options(info: dict) -> set:
    """从 /object_info 全量响应收集所有 loader 选项列表。

    ComfyUI 对 CheckpointLoaderSimple / UNETLoader / VAELoader / LoraLoader
    等模型选择输入，/object_info 里 spec[0] 是该输入的可选值清单
    （即已安装到对应 models/ 子目录的文件名列表）。全部收集后与
    required_models 做匹配，无需知道节点类型。
    """
    options: set = set()
    for node in (info or {}).values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("input")
        if not isinstance(inputs, dict):
            continue
        for section in ("required", "optional"):
            sect = inputs.get(section)
            if not isinstance(sect, dict):
                continue
            for spec in sect.values():
                if isinstance(spec, (list, tuple)) and spec:
                    first = spec[0]
                    if isinstance(first, (list, tuple)):
                        options.update(str(v) for v in first if str(v).strip())
    return options


def missing_models_from_info(info: Optional[dict], required_models: list) -> list:
    """在已获取的 object_info 上比对模型清单（端点复用同一份探测结果）。"""
    required = _norm_str_list(required_models)
    if not required:
        return []
    options = {_basename(o) for o in _iter_loader_options(info)}
    return [m for m in required if _basename(m) not in options]


def check_missing_models(client, required_models: list) -> tuple:
    """探测 ComfyUI 已安装模型，返回 (缺失模型列表, 探测是否成功)。

    - required_models 为空 → ([], True)
    - /object_info 不可达 → ([], False)（不阻断，与缺节点语义一致）
    - 响应里没有任何 loader 选项 → 视为探测不可信 → ([], False)，
      不虚构「已装齐」也不虚构「全缺」
    - 匹配规则：basename 精确相等（required_models 应填完整文件名）
    """
    if not _norm_str_list(required_models):
        return [], True
    try:
        info = client.object_info()
    except Exception:
        return [], False
    if not _iter_loader_options(info):
        return [], False
    return missing_models_from_info(info, required_models), True


def mode_supported(cap: Optional[dict], mode: str) -> bool:
    """模式是否被声明支持；cap 为 None（未声明）恒 True（兼容旧配置）。"""
    if not cap:
        return True
    return mode in cap["supports"]


def vram_warning(cap: Optional[dict], hw_profile: dict) -> Optional[str]:
    """本机显存低于声明下限时给出提示文本；否则 None。

    hw_profile 取 hw_probe.probe() 的字典输出（vram_gb 为 0 表示无独显）。
    """
    if not cap or not cap.get("min_vram_gb"):
        return None
    vram = float((hw_profile or {}).get("vram_gb") or 0)
    if vram >= cap["min_vram_gb"]:
        return None
    if vram <= 0:
        return (f"未检测到独显，而当前工作流（{cap.get('name') or '模板'}）"
                f"建议至少 {cap['min_vram_gb']:g}GB 显存；"
                f"{cap.get('vram_hint') or '可能无法在本地执行'}")
    return (f"本机显存 {vram:g}GB 低于当前工作流（{cap.get('name') or '模板'}）"
            f"建议的 {cap['min_vram_gb']:g}GB；{cap.get('vram_hint') or '生成可能失败或极慢'}")
