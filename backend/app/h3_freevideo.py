"""FreeVideo（VDN-H3）插件通道的权重档案与门槛核算。

## 为什么要单独一个模块

`engines/h3_quant.py` 算的是**本进程 diffusers 原生加载** MiniMax H3 原始权重
（bf16 123.8GiB，内存决定档位，52GB 内存起步）——对应引擎 ``h3_native``。

但 NMVideo 的 H3 正路是 ComfyUI 通道（引擎 ``h3`` → 节点 ``FreeVideoGenerate``），
跑的是 FreeVideo 插件自带的那套 **VDN-H3 8 步蒸馏权重**，玩法完全不同：

- 权重按 50 个 transformer 块切分，插件自己做「显存 → 主机内存 → 磁盘」三级
  放置，装不下的块每步从磁盘回读（docs/execution-planning.md）；
- 文本编码器在**独立进程**里跑完就退出，不和 transformer 抢内存；
- 所以官方门槛只有 **8GB 显存 / 16GB 内存**，而不是原生的 52GB 内存。

把这两条通道混成一个门槛，就会得出「8GB 显存 + 32GB 内存跑不了 H3」的错误
结论——本机实测就踩了这个坑。

## 数据来源（2026-10-30 取，写死是有意的）

安装前必须告诉用户「要下多大、差多少」，而这个体积在下载之前拿不到：

- 官方 README：8GB VRAM / 16GB RAM；四个资源包（视频模型 / 文本编码器 /
  视频与音频解码器 / 采样缓存）
- ``docs/execution-planning.md``：50 个 transformer 块、常驻单位 432.5MB、
  文本编码器独立进程、VAE 按块流式
- ``freevideo_engine/model_files.json`` @ ``751739e``：逐文件字节数——
  下面 ``PACKS`` 的体积是按它加总的，不是估的

体积或修订号变了改这里，并同步 ``MANIFEST_REVISION``。
"""
from dataclasses import dataclass, field
from typing import Tuple

#: 体积数据对应的 FreeVideo 模型清单修订号（OpenVDN/vdn-minimax-h3 @ 751739e）
MANIFEST_REVISION = "751739ee5b9e3ac802dca5d5111075fdaeb47885"

#: 官方最低配置（README：as little as 8 GB of VRAM and 16 GB of RAM）
MIN_VRAM_GIB = 8.0
MIN_RAM_GIB = 16.0

#: 下载缓存 / 首次准备权重的余量。资源包是原始 safetensors，不额外解压，
#: 但下载分片（partial）与插件生成的 int8 准备副产物要留位置。
DISK_HEADROOM_GIB = 8.0

#: 装上之后剩余少于这个值就提示「磁盘快满了」（不影响可用性）
DISK_TIGHT_GIB = 20.0


@dataclass(frozen=True)
class Pack:
    """一个资源包。``name`` 是功能名——界面只显示这个，不暴露模型名。"""

    id: str
    name: str
    size_gib: float
    why: str
    #: 精确字节数（来自 model_files.json），用于总量核算与「差多少」的提示
    nbytes: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size_gib": round(self.size_gib, 1),
            "why": self.why,
        }


# 四包体积 = model_files.json 逐文件加总（GiB）
#   视频模型        66,280,504,216 B = 61.73 GiB（14 个分片）
#   文本编码器      15,687,142,551 B = 14.61 GiB（nvfp4+awq 预量化）
#   视频与音频解码器 10,415,558,888 + 605,429,340 B = 10.26 GiB
#   采样与加速缓存   334,026,912 + 851,452,696 + 4,279,428,112 + 690,592,672 B = 5.73 GiB
PACKS: Tuple[Pack, ...] = (
    Pack(
        "video", "视频模型", 61.73,
        "生成画面的主体权重，按 50 块分片，按显存/内存/磁盘分级放置",
        66280504216,
    ),
    Pack(
        "text", "文本编码器", 14.61,
        "把提示词与参考图理解成模型输入，独立进程跑完即退出、不占推理内存",
        15687142551,
    ),
    Pack(
        "decoder", "视频与音频解码器", 10.26,
        "把潜变量还原成画面与声音，低显存下按块流式解码",
        10415558888 + 605429340,
    ),
    Pack(
        "cache", "采样与加速缓存", 5.73,
        "8 步蒸馏与画质提升所需的附加权重，装上才跑得动低步数",
        334026912 + 851452696 + 4279428112 + 690592672,
    ),
)


def total_gib() -> float:
    """四个资源包合计（GiB）。"""
    return sum(p.size_gib for p in PACKS)


def need_disk_gib() -> float:
    """装下 H3 插件通道所需的磁盘空间（资源包 + 余量）。"""
    return total_gib() + DISK_HEADROOM_GIB


def packs() -> list:
    return [p.to_dict() for p in PACKS]


@dataclass(frozen=True)
class FreeVideoPlan:
    """插件通道在本机的可执行结论。"""

    available: bool = False
    vram_gib: float = 0.0
    ram_gib: float = 0.0
    disk_free_gib: float = 0.0
    need_disk_gib: float = 0.0
    total_gib: float = 0.0
    blockers: tuple = field(default_factory=tuple)
    note: str = ""
    #: 磁盘装得下但余量不多：可用，但值得先提醒
    disk_tight: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "channel": "comfyui",
            "min_vram_gb": int(MIN_VRAM_GIB),
            "min_ram_gb": int(MIN_RAM_GIB),
            "need_disk_gb": round(self.need_disk_gib, 1),
            "weights_gb": round(self.total_gib, 1),
            "disk_free_gb": round(self.disk_free_gib, 1),
            "disk_tight": self.disk_tight,
            "note": self.note,
            "blockers": list(self.blockers),
            "packs": packs(),
        }


def _speed_note(ram_gib: float) -> str:
    """如实说明这一步会很慢，别让用户以为卡死了。

    官方表格只有 8GB+64GB 的实测（4060 Ti 8GB / 64GB：1344×768 10 秒约 603s）。
    32GB 内存能钉住的块更少，每步回读磁盘的块更多，只会更慢——不给具体秒数。
    """
    if ram_gib and ram_gib < 32:
        return ("8GB 显存 + 内存偏紧：大部分块每步从磁盘回读，10 秒 768p 出片可能要"
                "二十分钟以上，属于正常现象")
    return ("8GB 显存按块流式运行：官方实测 4060 Ti 8GB + 64GB 内存，"
            "1344×768 10 秒约 10 分钟；内存越少回读磁盘的块越多、越慢")


def plan(vram_gib: float, ram_gib: float, disk_free_gib: float = 0.0) -> FreeVideoPlan:
    """按「显存 × 内存 × 磁盘」判断插件通道能不能跑。

    门槛口径与原生通道**刻意不同**：这里不要求权重大部分驻留内存，
    插件会把装不下的块放磁盘按需回读，所以 16GB 内存也能起，只是慢。
    """
    vram = float(vram_gib or 0)
    ram = float(ram_gib or 0)
    disk = float(disk_free_gib or 0)
    need = need_disk_gib()

    blockers = []
    # 0 表示没探到（探测失败），不拿它当门槛——否则会告诉用户「你的显存是 0GB」
    if 0 < vram < MIN_VRAM_GIB:
        blockers.append(f"显存 {vram:.0f}GB，低于插件最低 {MIN_VRAM_GIB:.0f}GB")
    if 0 < ram < MIN_RAM_GIB:
        blockers.append(f"内存 {ram:.0f}GB，低于插件最低 {MIN_RAM_GIB:.0f}GB")
    if 0 < disk < need:
        blockers.append(
            f"磁盘可用 {disk:.0f}GB，装不下四个资源包（需 {need:.0f}GB，含余量）"
        )

    available = not blockers
    tight = available and 0 < disk and disk - need < DISK_TIGHT_GIB
    if not available:
        note = "；".join(blockers)
    elif vram <= 0 or ram <= 0:
        note = "未读到完整硬件信息，先按最低配置放行；装完再点「重新检测」复核"
    else:
        note = _speed_note(ram)

    return FreeVideoPlan(
        available=available,
        vram_gib=vram,
        ram_gib=ram,
        disk_free_gib=disk,
        need_disk_gib=need,
        total_gib=total_gib(),
        blockers=tuple(blockers),
        note=note,
        disk_tight=tight,
    )
