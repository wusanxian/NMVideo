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
- ``freevideo_engine/model_files.json`` @ ``751739e``：逐文件字节数

**2026-11-01 真机校正（重要，别再按 model_files.json 算）**

原来照 ``model_files.json`` 把「视频模型」记成 61.73GiB（14 个 bf16 分片），
合计 92.34GiB / 门槛 100.3GiB。真机装了 FreeVideo 之后实测，磁盘上根本不是这套：

- ``models/vdn/h3-base/transformer/`` 只有 ``config.json`` + ``index.json``，
  **一个权重分片都没有**——原版 bf16 transformer 从不下载；
- 真正参与推理的是 ``prepared/edge-da37681f5a1f7015/int8-convrot``
  （``OpenVDN/vdn-minimax-h3-edge`` 的 int8 预量化包，21.35GiB）。
  ``bundle.json`` 里 ``variants`` 写着 per_tensor / rowwise 两套各 22.9GB，
  引擎按架构自动挑一套下，所以不是 45.8GB 的双份；
- ``sampling-cache`` 实测 2.63GiB（清单里 4.28GB 那份是未压缩条目），
  upscaler 单文件 0.64GiB。

视频链路真实落盘 **49.50GiB（53.15GB）**，比清单口径少 43GiB。用旧门槛会把
磁盘够用的机器判成「装不下」——本机 97GB 可用就是这样被拦掉的。

不参与视频、因此**不计入门槛**的两项（FreeVideo 会一并装，但本程序不强制）：
``models/vdn/image_gen`` 16.11GiB（静图 / 角色卡）、``models/prompt-vlm`` 8.28GiB。

数值来自真机逐目录字节统计，改体积时请重新量，并同步 ``MEASURED_AT``。
"""
from dataclasses import dataclass, field
from typing import Tuple

#: 体积数据对应的 FreeVideo 模型清单修订号（OpenVDN/vdn-minimax-h3 @ 751739e）
MANIFEST_REVISION = "751739ee5b9e3ac802dca5d5111075fdaeb47885"

#: 上面这组体积是在哪台机器上量的（换机器/换量化格式要重测）
MEASURED_AT = "2026-11-01 · FreeVideo 2026.11.1.50531 · int8-convrot · G:/AI/FreeVideo"

#: 官方最低配置（README：as little as 8 GB of VRAM and 16 GB of RAM）
MIN_VRAM_GIB = 8.0
#: 本程序标定的内存线：32GB。官方机制 16GB 就能起，但内存越少，装不下的
#: transformer 块越多地走「磁盘回读」，一段 10 秒的片子会从十分钟变成几小时，
#: 中途还可能被判超时。承诺「能跑」就该按实际能用的线来标，16GB 归到「很慢」，
#: 不当作可用档对外说。
MIN_RAM_GIB = 32.0

#: 门槛比较的容差。32GB 机器实际报 31.96GB（固件预留 / 核显共享显存），
#: 严格 ``< 32`` 会把「正好 32GB」判成不够——与显存 8188MiB 报成 7.996GB
#: 是同一类坑：门槛要对齐「装了多少」，不是「系统报了多少」。
RAM_TOLERANCE_GIB = 0.6

#: 磁盘余量：下载中的 .incomplete 临时文件 + 运行时补生成的采样缓存 + 出片输出。
#: 权重包是「整个下完改名」，不会长期存在双份，所以 8GiB 够用，不必按体积翻倍。
DISK_HEADROOM_GIB = 8.0

#: 装上之后剩余少于这个值就提示「磁盘快满了」（不影响可用性）
DISK_TIGHT_GIB = 20.0


@dataclass(frozen=True)
class Pack:
    """一个资源包。``name`` 是功能名——界面只显示这个，不暴露模型名。"""

    id: str
    name: str
    why: str
    #: 精确字节数：真机逐目录统计得来，体积只从这里算，不另写一份小数免得对不上
    nbytes: int

    @property
    def size_gib(self) -> float:
        return self.nbytes / 2**30

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size_gib": round(self.size_gib, 1),
            "size_gb": round(self.nbytes / 1e9, 1),
            "why": self.why,
        }


# 四包体积 = 2026-11-01 真机实测字节（int8-convrot 落盘口径，不是 model_files 清单）
#   视频模型        22,928,807,665 B = 21.35 GiB  ← prepared/edge-.../int8-convrot
#   文本编码器      15,687,142,661 B = 14.61 GiB  ← qwen3vl_32b nvfp4+awq
#   视频与音频解码器 10,415,635,301 + 605,431,698 B = 10.26 GiB
#   采样与加速缓存    2,826,400,482 + 690,592,672 B = 3.28 GiB
# 合计 49.50 GiB（53.15 GB）。旧口径 92.34 GiB 是按 bf16 原始分片算的，见模块说明。
PACKS: Tuple[Pack, ...] = (
    Pack(
        "video", "视频模型",
        "生成画面的主体权重，int8 量化后按 50 块分片，按显存/内存/磁盘分级放置",
        22928807665,
    ),
    Pack(
        "text", "文本编码器",
        "把提示词与参考图理解成模型输入，独立进程跑完即退出、不占推理内存",
        15687142661,
    ),
    Pack(
        "decoder", "视频与音频解码器",
        "把潜变量还原成画面与声音，低显存下按块流式解码",
        10415635301 + 605431698,
    ),
    Pack(
        "cache", "采样与加速缓存",
        "8 步蒸馏与画质提升所需的附加权重，装上才跑得动低步数",
        2826400482 + 690592672,
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
    if ram_gib and ram_gib + RAM_TOLERANCE_GIB < MIN_RAM_GIB:
        return ("8GB 显存 + 内存偏紧：大部分块每步从磁盘回读，10 秒 768p 出片可能要"
                "二十分钟以上，属于正常现象")
    return ("8GB 显存按块流式运行：官方实测 4060 Ti 8GB + 64GB 内存，"
            "1344×768 10 秒约 10 分钟；32GB 内存回读的块更多，只会更慢，"
            "10 秒 768p 按二十分钟以上预期")


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
    if 0 < ram and ram + RAM_TOLERANCE_GIB < MIN_RAM_GIB:
        blockers.append(
            f"内存 {ram:.0f}GB，低于本程序标定的 {MIN_RAM_GIB:.0f}GB"
            "（插件机制 16GB 就能起，但内存不够时大部分块要走磁盘回读，"
            "一段 10 秒的片子得按小时算）"
        )
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
