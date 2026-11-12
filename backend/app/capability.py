"""能力分配：这台机器能跑什么模型、什么画布、什么画质档。

## 为什么需要这一层

「支持 / 不支持」这个二值判断在本产品里没有意义。同一套 H3 权重：

- 8GB 卡能启动，但画布封在 1344×768、单段 430 帧，10 秒片子按十分钟量级；
- 24GB 卡可以放到 1920×1088 / 855 帧，20 步单遍也扛得住。

用户看到的必须是一个结论（「你这台能跑标准档，1344×768，别超 17 秒」），
而不是五个引擎各自给一句「显存不足」。所以能力判定收在一处，
界面只展示这里算出来的东西。

## 边界（2026-11-12 定）

只走 ComfyUI。判据是「能不能只用 ComfyUI 的 HTTP 接口驱动完」：

- ``/object_info`` 里有没有对应节点 —— 没有就是不可用，不找旁路；
- 权重体积算进磁盘门槛 —— 装不下就如实说装不下；
- 需要调第三方 REST 服务 / 读它的 token / 依赖它的进程 —— 一律不列。

## 数据来源

- 体积：``h3_freevideo``（H3 四包，真机逐目录字节）与本模块 ``_EXTRA_PACKS``
  （FreeVideo 目录实测的另外三套：Qwen-Image 生图 / H3 Image / prompt-vlm）
- 画布与帧数上限：``h3_protocol.LIMITS``（按显存分档，不在这里重写一份）
- 显存/内存：优先取 ComfyUI 服务端（``h3_server.probe``），探不到退回本机
  ``hw_probe`` —— H3 跑在 ComfyUI 那台机上，按本机卡限死是错的
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import h3_freevideo, h3_protocol, hw_probe

#: 不在 H3 四包内、但同一安装目录下实测过的另外三套权重（GiB）
#: 2026-11-12 于 ``G:/AI/FreeVideo/FreeVideo-engine/models`` 逐目录统计
QWEN_IMAGE_GIB = 16.11        # vdn/image_gen：Qwen-Image 2.1 Turbo int8 + 编码器 + VAE
H3_IMAGE_GIB = 20.65          # h3-image：DiT 19.54 + core 1.11（文本编码器复用 H3 的）
PROMPT_VLM_GIB = 8.28         # prompt-vlm：Qwen3-VL 8B，替用户写提示词

#: 输出与运行期的额外余量（不含权重下载余量，那个在 h3_freevideo 里）
OUTPUT_HEADROOM_GIB = 5.0


# ------------------------------------------------------------------ 机型分档
@dataclass(frozen=True)
class MachineTier:
    id: str
    name: str
    min_vram_gib: float
    min_ram_gib: float
    #: 该档允许的画质档 id，按开销从低到高
    quality: Tuple[str, ...]
    #: 能否与生图/白模同时跑（显存够不够再塞一个模型）
    concurrent: bool
    note: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "min_vram_gib": self.min_vram_gib,
            "min_ram_gib": self.min_ram_gib,
            "quality": list(self.quality),
            "concurrent": self.concurrent,
            "note": self.note,
        }


#: 四档。显存是主判据（决定画布与步数），内存是次判据（决定回读多少块）。
#: 12GB 与 8GB 同画布档——画布上限本身按 16GB 分界，硬凑第三档只会编数字。
TIERS: Tuple[MachineTier, ...] = (
    MachineTier("xl", "工作站", 24.0, 64.0,
                ("light", "medium", "high", "max"), True,
                "画布放满 1920×1088，可与生图/白模并发"),
    MachineTier("l", "进阶", 16.0, 48.0,
                ("light", "medium", "high", "max"), True,
                "画布 1920×1088，四档画质全开，极致档推荐"),
    MachineTier("m", "标准", 12.0, 32.0,
                ("light", "medium", "high"), False,
                "同 8GB 画布档，但步缓存命中更好，精细档可跑偏慢"),
    MachineTier("s", "入门", 0.0, 32.0,
                ("light", "medium"), False,
                "1344×768 / 430 帧上限，10 秒片子按十分钟以上预期"),
)


def pick_tier(vram_gib: float, ram_gib: float) -> MachineTier:
    """按显存 → 内存选档。内存不达标就往下退一档，不退到无档。

    内存影响的是「要回读多少块」，不是「能不能跑」。8GB 卡配 16GB 内存仍然能出片，
    只是慢到不建议，所以退档给更保守的画质建议，而不是判死。
    """
    vram = float(vram_gib or 0)
    for t in TIERS:  # TIERS 已按显存从高到低排
        if vram + 0.5 >= t.min_vram_gib:  # 容差：8188MiB 会报成 7.996GB
            return t
    return TIERS[-1]


# ------------------------------------------------------------------ 模型台账
@dataclass(frozen=True)
class ModelEntry:
    """一个模型在「只走 ComfyUI」下的定位。

    ``status`` 是结构性的（跟机器无关）：
      - ``ok``          有节点、有路径，装得下就能跑
      - ``unverified``  路在界内，但没真机跑通，不能对外说支持
      - ``blocked``     ComfyUI 里没有入口，再怎么装也用不了
      - ``retired``     走进程内推理，与新边界冲突，计划下线
    """

    id: str
    name: str
    why: str
    size_gib: float
    #: ComfyUI 侧的入口。``None`` 表示没有入口
    entry: Optional[str]
    status: str
    #: 对应缺口编号（见 docs/reports/20261112-comfy-only-boundary-and-capacity.md）
    gap: str = ""
    #: 结构性的「为什么不可用」，会原样展示给用户
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "why": self.why,
            "size_gib": self.size_gib,
            "entry": self.entry,
            "status": self.status,
            "gap": self.gap,
            "reason": self.reason,
        }


def catalog() -> List[ModelEntry]:
    """模型台账。体积只在 ``h3_freevideo`` 与本模块常量里各写一份，不重复。"""
    h3_size = round(h3_freevideo.total_gib(), 2)
    return [
        ModelEntry(
            "h3-video", "视频生成（H3 8 步蒸馏）",
            "生成画面与声音的主体权重，50 块分片按显存/内存/磁盘三级放置",
            h3_size, "FreeVideoGenerate", "ok", "",
        ),
        ModelEntry(
            "qwen-image-2.1", "本地生图（Qwen-Image 2.1）",
            "角色卡、场景图、分镜首帧；不联网也能出图",
            QWEN_IMAGE_GIB, "UNETLoader + CLIPLoader + VAELoader", "unverified", "G6",
            "路在界内（ComfyUI 原生节点），但还没在本机跑通过，暂不当能力承诺",
        ),
        ModelEntry(
            "h3-image", "静图生成（H3 Image）",
            "H3 侧的静图能力，与视频共用文本编码器",
            H3_IMAGE_GIB, None, "blocked", "G4",
            "ComfyUI 没有对应节点，且本体不认这套 DiT，装了也调不到",
        ),
        ModelEntry(
            "prompt-vlm", "本地提示词模型（Qwen3-VL 8B）",
            "断网时替用户扩写提示词",
            PROMPT_VLM_GIB, None, "blocked", "G5",
            "只有文本编码器身份，ComfyUI 里没有能当 LLM 用的节点",
        ),
        ModelEntry(
            "white-model", "白模（人体剪影）",
            "把真人动作压成剪影，用来验证构图与运镜",
            0.0, "SAM3DBody_*", "ok", "",
        ),
        ModelEntry(
            "wan-ti2v", "低显存出片权重（Wan 2.2 TI2V-5B）",
            "历史兜底通道，进程内推理",
            32.0, None, "retired", "G7",
            "不走 ComfyUI，与新边界冲突；代码保留，界面不再引导",
        ),
        ModelEntry(
            "anime", "动漫底模",
            "历史兜底通道，进程内推理",
            5.0, None, "retired", "G7",
            "同上",
        ),
    ]


# ------------------------------------------------------------------ 判定
def _probe_hardware(comfy_url: Optional[str] = None) -> dict:
    """显存/内存口径：ComfyUI 服务端优先，探不到退回本机。

    返回里的 ``source`` 会被界面直接展示（``server`` / ``local``），
    因为「按本机估算」和「按服务端实测」对用户的含义不同。
    """
    try:
        srv = h3_server_probe(comfy_url)
    except Exception:  # noqa: BLE001 - 探测失败不该让能力接口 500
        srv = {}
    if srv.get("reachable") and float(srv.get("vram_gib") or -1) > 0:
        return {
            "vram_gib": round(float(srv["vram_gib"]), 2),
            "ram_gib": round(float(srv.get("ram_gib") or 0), 2),
            "gpu_name": srv.get("gpu_name") or "",
            "source": "server",
        }
    try:
        prof = hw_probe.probe()
    except Exception:  # noqa: BLE001
        return {"vram_gib": 0.0, "ram_gib": 0.0, "gpu_name": "", "source": "unknown"}
    return {
        "vram_gib": round(float(prof.vram_gb or 0), 2),
        "ram_gib": round(float(prof.ram_gb or 0), 2),
        "gpu_name": prof.gpu_name or "",
        "source": "local",
    }


def h3_server_probe(comfy_url: Optional[str]) -> dict:
    """薄包装，便于单测替换（不在模块层 import 具体实现）。"""
    from . import h3_server

    return h3_server.probe(comfy_url)


def _limits_for(vram_gib: float) -> dict:
    """画布与帧数上限，直接取 ``h3_protocol`` 的标定表，不重算。"""
    for min_vram, max_pixels, max_frames, name in h3_protocol.LIMITS:
        if float(vram_gib or 0) + 0.5 >= min_vram:
            return {
                "max_pixels": max_pixels,
                "max_frames": max_frames,
                "label": name,
                # 1344×768 是 7:4，别按 16:9 反推
                "max_size": h3_protocol.calibrated_canvas(max_pixels),
            }
    return {"max_pixels": 0, "max_frames": 0, "label": "未知", "max_size": (0, 0)}


def assess(vram_gib: float = 0.0, ram_gib: float = 0.0,
           disk_free_gib: float = 0.0,
           nodes: Optional[List[str]] = None,
           comfy_url: Optional[str] = None) -> dict:
    """这台机器能跑什么。

    ``nodes`` 是 ComfyUI ``/object_info`` 的节点名集合。传 ``None`` 表示没探到
    服务端——此时**不能**判定「节点缺失」，只能说「没连上」，否则会把
    「ComfyUI 没启动」错报成「插件没装」，用户会去重装插件。
    """
    hw = _probe_hardware(comfy_url)
    vram = float(vram_gib or hw["vram_gib"])
    ram = float(ram_gib or hw["ram_gib"])
    tier = pick_tier(vram, ram)
    limits = _limits_for(vram)

    video_need = round(h3_freevideo.need_disk_gib(), 1)
    full_need = round(video_need + QWEN_IMAGE_GIB + H3_IMAGE_GIB
                      + PROMPT_VLM_GIB + OUTPUT_HEADROOM_GIB, 1)

    has_nodes = nodes is not None
    node_set = set(nodes or ())

    # 磁盘结论：先看视频链路（这是主路），再看全量
    disk_state = "unknown"
    if disk_free_gib:
        if disk_free_gib + 0.5 >= full_need:
            disk_state = "full"
        elif disk_free_gib + 0.5 >= video_need:
            disk_state = "video_only"
        else:
            disk_state = "insufficient"

    models = []
    for m in catalog():
        item = m.to_dict()
        avail, reason = m.status, m.reason

        if m.id == "h3-video":
            if not has_nodes:
                avail, reason = "unknown", "没连上 ComfyUI，无法确认节点是否齐全"
            elif "FreeVideoGenerate" not in node_set:
                avail = "blocked"
                reason = ("ComfyUI 里没有 FreeVideoGenerate 节点，H3 无法执行"
                          "（该节点来自 FreeVideo 插件，见缺口 G3）")
            elif disk_state == "insufficient":
                avail = "blocked"
                reason = f"磁盘不足：需要 {video_need} GiB，剩余 {round(disk_free_gib, 1)} GiB"
            elif vram + 0.5 < h3_freevideo.MIN_VRAM_GIB:
                avail = "blocked"
                reason = f"显存低于 {int(h3_freevideo.MIN_VRAM_GIB)}GB，插件通道起不来"
            elif ram + h3_freevideo.RAM_TOLERANCE_GIB < h3_freevideo.MIN_RAM_GIB:
                # 能起但很慢：给 degraded 而不是 blocked，别把「慢」说成「不能」
                avail = "degraded"
                reason = (f"内存 {round(ram, 1)}GB 低于建议的 "
                          f"{int(h3_freevideo.MIN_RAM_GIB)}GB：每步要回读更多块，"
                          "10 秒片子按二十分钟以上预期")
            else:
                avail = "available"

        elif m.id == "qwen-image-2.1":
            if not has_nodes:
                avail, reason = "unknown", "没连上 ComfyUI，无法确认节点是否齐全"
            elif disk_state == "insufficient":
                avail = "blocked"
                reason = (f"磁盘不足：生图需在视频链路之外再加 {QWEN_IMAGE_GIB} GiB")
            else:
                avail, reason = "unverified", m.reason

        elif m.id == "white-model":
            if not has_nodes:
                avail, reason = "unknown", "没连上 ComfyUI，无法确认节点是否齐全"
            elif any(n.startswith("SAM3DBody") for n in node_set):
                avail, reason = "available", ""
            else:
                avail, reason = "blocked", "ComfyUI 里没有 SAM3DBody 节点族"

        item.update({"availability": avail, "availability_reason": reason})
        models.append(item)

    return {
        "tier": tier.to_dict(),
        "hardware": {**hw, "vram_gib": round(vram, 2), "ram_gib": round(ram, 2)},
        "limits": {
            **limits,
            "max_size": list(limits["max_size"]),
            # 帧数必须 17n+5 且 ≥39；上限只是标定，实际还受这个约束
            "frame_step": 17,
            "frame_min": 39,
        },
        "disk": {
            "free_gib": round(float(disk_free_gib or 0), 1),
            "video_only_gib": video_need,
            "full_gib": full_need,
            "state": disk_state,
        },
        "quality_tiers": [t for t in tier.quality
                          if t in h3_protocol.QUALITY_TIERS],
        "models": models,
        "comfy_connected": has_nodes,
    }
