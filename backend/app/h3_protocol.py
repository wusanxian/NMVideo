"""H3 插件节点的协议适配（FreeVideoGenerate / FreeVideoReference / FreeVideoLoRA），
与 Wan 的采样规则分开处理。

协议来源：FlashML-org/FreeVideo freevideo_engine/comfy_nodes.py（2026-10-20）。
该插件自行管理模型、内存与原生两遍采样；本模块只构建 ComfyUI 输入。
节点类名（FreeVideoGenerate 等）是 ComfyUI 侧插件的外部契约，不随本项目改名。

## 质量档位（2026-11-01 对齐 FreeVideo 官方四档）

FreeVideo 的「画质档」不是换模型，而是 **步数 + 两遍加速 + 步缓存** 的组合。
节点真实输入（``/object_info/FreeVideoGenerate`` 拉到的契约）已确认：

    base_steps=8 → 走两遍加速（首遍半尺寸，再放大二遍）
    base_steps≠8 → 走单遍采样这么多步（两遍加速失效）

所以「20 步 + 两遍」这种组合并不存在，档位只能这样配：

| 档位 | base_steps | two_pass | upscale | step_cache | 说明 |
|---|---|---|---|---|---|
| light  | 8  | True  | 2   | none        | 最快，试构图（E01 用的就是这档） |
| medium | 8  | True  | 1.5 | none        | 首遍尺寸更大，细节更多 |
| high   | 16 | False | —   | easycache-av | 单遍 16 步，画质优先 |
| max    | 20 | False | —   | easycache-av | 单遍 20 步，最慢最好 |

## 画布与时长上限

插件自己不拦规模和时长（``geometry`` 只要求边长 ≥256 且是 32 的倍数），
所以上限是本程序按显存**标定**的，不是节点硬门槛：

| 显存 | 画布上限 | 单段帧数上限 |
|---|---|---|
| < 16GB（8GB 档） | 1344×768（≈103 万像素） | 430 帧（约 17 秒） |
| ≥ 16GB | 1920×1088（≈209 万像素） | 855 帧（约 35 秒） |

**这里的显存取 ComfyUI 服务端的，不是本机的**（见 ``h3_server``）：H3 的推理
跑在 ComfyUI 那台机上，8GB 笔记本连 24GB 工作站按 8GB 限死是浪费，反过来
24GB 机器连 8GB 服务器放行 1080p 会让对面 OOM。服务端探不到才退回本机口径，
并在界面上标明「按本机估算」——不让用户以为这个上限是拍出来的。

超过标定规模在对应卡上会 OOM 或慢到不可用，所以在归一阶段就拦住并给出
可执行的替代方案（拆段续接、降分辨率、换档位），而不是默默截断——存档参数
必须等于实际输出，否则用户拿到 8 秒片却以为自己要了 17 秒，问题会被记到
「引擎不稳定」上。
"""
import copy
import json
import math
import time
from typing import Optional, Tuple

from . import comfyui
from . import h3_server

#: 档位表：字段名与节点输入一一对应，直接注入，不做二次翻译
QUALITY_TIERS = {
    "light": {
        "name": "轻量",
        "desc": "8 步两遍 · 首遍半尺寸再放大 ×2 —— 最快，用来试构图",
        "two_pass": True,
        "base_steps": 8,
        "refine_steps": 3,
        "upscale_factor": "2",
        "step_cache": "none",
    },
    "medium": {
        "name": "标准",
        "desc": "8+3 步两遍 · 首遍尺寸更大、放大 ×1.5 —— 细节更多",
        "two_pass": True,
        "base_steps": 8,
        "refine_steps": 3,
        "upscale_factor": "1.5",
        "step_cache": "none",
    },
    "high": {
        "name": "精细",
        "desc": "16 步单遍 + 步缓存 —— 画质优先，耗时约两倍",
        "two_pass": False,
        "base_steps": 16,
        "refine_steps": 3,
        "upscale_factor": "2",
        "step_cache": "easycache-av",
    },
    "max": {
        "name": "极致",
        "desc": "20 步单遍 + 步缓存 —— 最慢最好，8GB 卡上以小时计",
        "two_pass": False,
        "base_steps": 20,
        "refine_steps": 3,
        "upscale_factor": "2",
        "step_cache": "easycache-av",
    },
}

#: 默认档：8 步两遍。旧任务没有 h3_quality 字段时也落到这里，
#: 与 0.1.10 之前「steps 恒等于 8」的行为完全一致。
DEFAULT_TIER = "light"

#: 画布 / 时长上限按显存分档标定：(显存下限, 画布像素上限, 单段帧数上限, 档名)
#: 8GB 档照 FreeVideo 官方标定（1344×768 + light 单段 17 秒 / 430 帧）；
#: 大显存档放宽到 2 倍像素，帧数上限同比例（855 = 5 + 17×50）。
LIMITS = (
    (16.0, 1920 * 1088, 855, "大显存档"),
    (0.0, 1344 * 768, 430, "8GB 档"),
)

#: 上限结果的缓存：探测一次 ComfyUI 要发 HTTP，提交任务是高频路径。
#: 但服务端可能换机、ComfyUI 地址可能改，所以带 TTL 而不是记一辈子。
#: 任务自带 ``comfy_url`` 时（可以连别的服务）按 url 分开缓存。
_limits_cache: Optional[Tuple[float, int, int, str]] = None
_limits_ts = 0.0
_per_url: dict = {}
#: 判定依据（显存、来源、显卡名、服务端地址），按 url 记，给界面解释上限从哪来
_basis_by_url: dict = {}
_LIMITS_TTL = 60.0


def _probe_vram(base_url: str = "") -> Tuple[float, str, str]:
    """判定用的显存 → (GiB, 来源, 显卡名)。

    H3 跑在 ComfyUI 上，先问服务端；服务端不可达才退回本机。返回的来源字符串
    会被界面直接展示，所以必须诚实：``server`` / ``local`` / ``unknown``。
    """
    key = (base_url or "").rstrip("/")
    try:
        srv = h3_server.probe(base_url or None)
    except Exception:  # noqa: BLE001 - 探测失败不影响出片
        srv = {}
    basis = {"comfy_url": srv.get("base_url") or key}
    if srv.get("reachable") and float(srv.get("vram_gib") or -1) > 0:
        vram = float(srv["vram_gib"])
        basis.update({
            "vram_source": "server",
            "gpu_name": srv.get("gpu_name") or "",
            "server_ram_gib": srv.get("ram_gib", -1),
            "_vram": vram,
        })
        _basis_by_url[key] = basis
        return vram, "server", srv.get("gpu_name") or ""
    try:
        from . import hw_probe

        local = hw_probe.vram_total_gb()
    except Exception:  # noqa: BLE001
        local = -1.0
    source = "local" if local > 0 else "unknown"
    basis.update({
        "vram_source": source, "gpu_name": "", "server_ram_gib": -1,
        "_vram": local if local > 0 else -1.0,
    })
    _basis_by_url[key] = basis
    return (local if local > 0 else -1.0), source, ""


def _row_for(vram: float) -> Tuple[float, int, int, str]:
    for row in LIMITS:
        if vram >= row[0]:
            return row
    return LIMITS[-1]


def limits(base_url: Optional[str] = None) -> Tuple[float, int, int, str]:
    """当前适用的 (显存下限, 画布像素上限, 帧数上限, 档名)。

    显存优先取 ComfyUI 服务端；探测不到才按本机算。两者都拿不到时按 8GB 档
    处理——保守一侧，宁可提示「上限 1344×768」，也不要在没显存信息的机器上
    放行 1080p 然后 OOM。

    ``base_url`` 给定时按那台服务算：任务可以自带 ``comfy_url`` 连别的 ComfyUI，
    拿默认服务的显存去卡它同样是错的。
    """
    global _limits_cache, _limits_ts
    url = (base_url or "").rstrip("/")
    is_default = not url or url == comfyui._default_base_url().rstrip("/")
    now = time.time()
    if is_default:
        if _limits_cache is not None and (not _limits_ts or now - _limits_ts < _LIMITS_TTL):
            return _limits_cache
    else:
        hit = _per_url.get(url)
        if hit and now - hit[0] < _LIMITS_TTL:
            return hit[1]
    vram, _, _ = _probe_vram(url)
    row = _row_for(vram)
    if is_default:
        _limits_cache, _limits_ts = row, now
    else:
        _per_url[url] = (now, row)
    return row


def reset_limits() -> None:
    """改 ComfyUI 地址、或手动要求重新探测时调用。"""
    global _limits_cache, _limits_ts
    _limits_cache = None
    _limits_ts = 0.0
    _per_url.clear()
    _basis_by_url.clear()
    h3_server.reset_cache()


def limits_info(base_url: Optional[str] = None) -> dict:
    """上限的判定依据（给界面解释「为什么只能到这里」）。"""
    row = limits(base_url)
    basis = _basis_by_url.get((base_url or "").rstrip("/")) or {}
    vram = basis.get("_vram")
    return {
        "tier": row[3],
        "max_pixels": row[1],
        "max_frames": row[2],
        # 读不到就是 null，别把哨兵值 -1 当显存透出去（界面会显示「-1GB」）
        "vram_gib": round(vram, 2) if vram and vram > 0 else None,
        "vram_source": basis.get("vram_source") or "unknown",
        "gpu_name": basis.get("gpu_name") or "",
        "comfy_url": basis.get("comfy_url") or "",
        "server_ram_gib": basis.get("server_ram_gib", -1),
    }


def tiers() -> list:
    """给界面用的档位清单。"""
    return [
        {
            "id": key,
            "name": tier["name"],
            "desc": tier["desc"],
            # 界面小字：真实生效的参数，别拿「20 步 + 两遍」这种不存在的组合糊弄人
            "params": (
                f"{tier['base_steps']} 步"
                + (" 两遍" if tier["two_pass"] else " 单遍")
                + (f" · 放大 ×{tier['upscale_factor']}" if tier["two_pass"] else "")
                + (" · 步缓存" if tier["step_cache"] != "none" else "")
            ),
            "base_steps": tier["base_steps"],
            "two_pass": tier["two_pass"],
        }
        for key, tier in QUALITY_TIERS.items()
    ]


def resolve_tier(params: dict) -> dict:
    """取档位定义；未知值退回默认档（不报错——旧存档里可能有别的写法）。"""
    tier_id = str(params.get("h3_quality") or "").strip().lower()
    return QUALITY_TIERS.get(tier_id) or QUALITY_TIERS[DEFAULT_TIER]


#: 各档的标定画幅（界面第一个推荐值）。8GB 档就是 FreeVideo 官方标的
#: 1344×768——注意它是 7:4，不是 16:9；按 16:9 反推只能得到 1344×736，
#: 比标定少一档，用户点下去就永远拿不到官方那个画幅。
_CANVAS_BY_PIXELS = {
    1344 * 768: (1344, 768),
    1920 * 1088: (1920, 1088),
}


def _canvas_options(pixels: int) -> list:
    """按上限反推常见画幅，边长取 32 的整数倍。

    有标定画幅时先给标定值，再补竖屏与方形；没有才按 16:9 推。
    """
    out = []
    native = _CANVAS_BY_PIXELS.get(pixels)
    if native:
        out.append(list(native))
    aspects = (9 / 16, 1.0) if native else (16 / 9, 9 / 16, 1.0)
    for aspect in aspects:
        width = int(math.sqrt(pixels * aspect) // 32 * 32)
        height = int((width / aspect) // 32 * 32)
        if width >= 256 and height >= 256 and [width, height] not in out:
            out.append([width, height])
    return out


def payload(base_url: Optional[str] = None) -> dict:
    """界面要的一整包：档位清单 + 当前上限 + 上限的判定依据。"""
    _, max_pixels, max_frames, tier_name = limits(base_url)
    info = limits_info(base_url)
    return {
        "tiers": tiers(),
        "default": DEFAULT_TIER,
        "limits": {
            "tier": tier_name,
            "max_pixels": max_pixels,
            "max_frames": max_frames,
            "max_seconds": round(max_frames / 24, 1),
            "canvas_options": _canvas_options(max_pixels),
            # 依据一起返回：界面要能说清「上限是按哪台机器、什么量化定的」，
            # 而不是甩一句「超上限」让用户自己猜
            "basis": {k: v for k, v in info.items()
                      if k not in ("tier", "max_pixels", "max_frames")},
        },
    }


def is_h3_protocol(template: dict) -> bool:
    return (template.get("_meta") or {}).get("model_id") == "vdn-minimax-h3"


def _basis_note(base_url: str = "") -> str:
    """上限依据的一句话说明，拼在拒绝信息后面。"""
    info = limits_info(base_url)
    src = info.get("vram_source")
    if src == "server":
        who = info.get("gpu_name") or "ComfyUI 服务端"
        return f"上限按 ComfyUI 服务端 {who} 标定"
    if src == "local":
        return "上限按本机显卡估算（ComfyUI 服务端暂不可达）"
    return "显存读不到，按最低档保守标定"


def _too_big(params: dict) -> None:
    """超过当前档位的上限就明确拒绝，并给一条能走的路。

    不悄悄缩、不悄悄截：存档参数必须和实际输出一致，否则用户拿到 8 秒片
    却以为自己要了 17 秒，问题会被记到「引擎不稳定」上。
    """
    # 按这个任务实际要连的 ComfyUI 算：params 里带着 comfy_url 时可能不是默认服务
    url = str(params.get("comfy_url") or "")
    _, max_pixels, max_frames, tier_name = limits(url)
    note = _basis_note(url)
    pixels = int(params["width"]) * int(params["height"])
    if pixels > max_pixels:
        raise ValueError(
            f"{tier_name}的画布上限是 {max_pixels} 像素（如 1344×768 或 768×1344），"
            f"当前 {params['width']}×{params['height']} = {pixels} 像素。"
            f"请调小分辨率——H3 跑到标定画布已经是满负荷。{note}。"
        )
    frames = int(params["num_frames"])
    if frames > max_frames:
        seconds = frames / max(1, int(params.get("fps") or 24))
        raise ValueError(
            f"{tier_name}的单段上限是 {max_frames} 帧（约 {int(max_frames / 24)} 秒），"
            f"当前 {frames} 帧（约 {seconds:.0f} 秒）。"
            "更长的镜头请拆成两段，用「续接」以上一段的末帧继续（场景与角色能接上）。"
            f"{note}。"
        )


def normalize_request(params: dict) -> None:
    if params.get("preview"):
        params.update(json.loads(params.get("generation_target") or "{}"))
    params["width"] = math.ceil(params["width"] / 32) * 32
    params["height"] = math.ceil(params["height"] / 32) * 32
    requested_frames = params["num_frames"] / params["fps"] * 24
    params["num_frames"] = 5 + 17 * max(2, math.ceil((requested_frames - 5) / 17))
    params["fps"] = 24

    # 档位决定步数与两遍加速（节点契约：非 8 步时两遍加速自动失效）
    tier = resolve_tier(params)
    params["h3_quality"] = next(
        (k for k, v in QUALITY_TIERS.items() if v is tier), DEFAULT_TIER)
    params["steps"] = tier["base_steps"]
    params["two_pass"] = tier["two_pass"]
    params["refine_steps"] = tier["refine_steps"]
    params["upscale_factor"] = tier["upscale_factor"]
    params["step_cache"] = tier["step_cache"]

    _too_big(params)

    params["preview_kind"] = "native" if params.get("preview") else "rerun"
    if params.get("preview"):
        # 目标档位也要带上：二采时按同一个 h3_quality 继续，否则会掉回 light
        params["generation_target"] = json.dumps({
            key: params[key] for key in
            ("width", "height", "num_frames", "fps", "steps", "guidance", "h3_quality")
        })


def attach_characters(graph: dict, params: dict, client, *, upload_image=None) -> dict:
    """将冻结角色卡里的参考图与 LoRA 接入官方 Reference / LoRA 节点。"""
    graph = copy.deepcopy(graph)
    upload_image = upload_image or (lambda url: comfyui.upload_reference_image(client, url))
    generators = [key for key, node in graph.items() if node.get("class_type") == "FreeVideoGenerate"]
    if len(generators) != 1:
        raise ValueError("H3 节点图需要且只能包含一个 FreeVideoGenerate 输出节点")
    characters = json.loads(params.get("characters_snapshot") or "[]")
    images = []
    loras = []
    for character in characters:
        for value in ((character.get("reference_images") or []) if params.get("use_character_images", True) else []):
            url = value.get("url", "") if isinstance(value, dict) else value
            if url and url not in images:
                images.append(url)
        if params.get("use_character_lora", True) and character.get("lora") and character["lora"] not in loras:
            loras.append(character["lora"])
    if len(images) > 32:
        raise ValueError("H3 最多支持 32 个参考输入，请减少角色参考图")
    target = graph[generators[0]]["inputs"]
    if images and (target.get("first") or target.get("last")) and not target.get("continue_from"):
        raise ValueError("H3 不支持首尾帧与角色参考图混用，请关闭本次角色参考图或改用文生视频参考模式；角色 LoRA 可继续使用")
    required = (["LoadImage", "FreeVideoReference"] if images else []) + (["FreeVideoLoRA"] if loras else [])
    if required:
        from .workflow_capabilities import check_missing_nodes, check_missing_models
        missing, ok = check_missing_nodes(client, required)
        if not ok or missing:
            raise ValueError(f"无法确认角色条件节点可用：{'、'.join(missing or required)}")
        missing, ok = check_missing_models(client, loras)
        if loras and (not ok or missing):
            raise ValueError(f"无法确认角色 LoRA 已安装：{'、'.join(missing or loras)}")

    def add_node(kind, inputs):
        index = 1
        while str(index) in graph:
            index += 1
        key = str(index)
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    reference = target.get("references")
    for url in images:
        image = add_node("LoadImage", {"image": upload_image(url)})
        inputs = {"image": image}
        if reference:
            inputs["previous"] = reference
        reference = add_node("FreeVideoReference", inputs)
    if images:
        target["references"] = reference
    adapter = target.get("loras")
    for name in loras:
        inputs = {"lora": name, "strength": 1.0}
        if adapter:
            inputs["previous"] = adapter
        adapter = add_node("FreeVideoLoRA", inputs)
    if loras:
        target["loras"] = adapter
    return graph
