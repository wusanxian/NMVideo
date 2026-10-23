"""H3 量化分级：把「显存 × 内存」换算成可执行的加载方案。

## 为什么要这个模块

H3 的两个权重都极大（体积实测自 HF 文件清单，2026-10-23）：

    61.7 GiB  transformer（14 个分片，bf16）
    62.1 GiB  Qwen3-VL-32B 文本编码器（14 个分片，bf16）
    ------------------------------------------------
   123.8 GiB  合计（磁盘 139 GB）

而 ``apply_group_offloading`` 的实现是**把权重放在 CPU 内存**、按需搬进显存，
它省显存、**不省内存**。所以低显存档位真正的门槛是内存，不是显存：

    - 8GB 显存 + 32GB 内存 → 差 4 倍内存，任何显存技巧都救不回来
    - 8GB 显存 + 64GB 内存 → int4 量化后约 38 GiB，可行
    - 24GB 显存 + 128GB 内存 → int8 约 62 GiB，可行

本模块只做「算账 + 给出可执行档位」，不 import torch / torchao，
这样单测在无 GPU、甚至无 torchao 的环境里也能跑。

## 档位（内存决定精度上限，显存决定常驻/流式与画布）

| tier | 触发条件（可用内存） | 量化 | 权重(GiB) | 最低内存 |
|---|---|---|---|---|
| full  | ≥ 140GB | 无   | 123.8 | 140GB |
| int8  | ≥ 79GB  | int8 | 62.5  | 79GB  |
| int4  | ≥ 52GB  | int4 | 37.7  | 52GB  |
| 不可用 | < 52GB | —    | —     | —     |

显存只影响两件事：权重能否常驻（`vram ≥ 权重+6` 则常驻，否则按块流式），
以及画布上限（<10GB → 196608px / <24GB → 524288px / 其余 1032192px）。
帧数压不下去：模型最短 5.17 秒 = 124 帧，这是激活体积的硬下限。
"""
from dataclasses import dataclass, field
from typing import Dict

# ---- 权重体积（GiB，bf16 实测）----
WEIGHT_TRANSFORMER_GIB = 61.7
WEIGHT_TEXT_ENCODER_GIB = 62.1
WEIGHT_BASE_GIB = WEIGHT_TRANSFORMER_GIB + WEIGHT_TEXT_ENCODER_GIB

# ---- 量化压缩比（相对 bf16）----
# int8：每参 1 字节 + 少量 scale；int4(group_size=128)：每参 0.5 字节 + 分组 scale
QUANT_FACTOR = {"none": 1.0, "int8": 0.505, "int4": 0.305}

# ---- 内存余量：系统占用 + 激活中转 + 分片 mmap 抖动 ----
HEADROOM_GIB = {"none": 16.0, "int8": 16.0, "int4": 14.0}

# ---- 画布上限（像素）：低显存下激活才是瓶颈，帧数又压不下去 ----
PIXELS_DEFAULT = 1032192        # 1344×768 级（引擎既有上限，不抬高）
PIXELS_LOW_VRAM = 524288        # 约 720×720 / 896×584，10~23GB 显存档
PIXELS_TINY = 196608            # 512×384，8GB 档

# H3 最低显存档位（激活下限，与权重无关）
MIN_VRAM_GIB = 6.0

# 各量化方式要求的最低算力（torchao weight-only kernel）
MIN_CAP = {"int8": 70, "int4": 80}

TIER_LABEL = {
    "full": "全精度",
    "int8": "int8 权重量化",
    "int4": "int4 权重量化",
}


@dataclass(frozen=True)
class H3QuantPlan:
    """一次可执行的加载方案。available=False 时 reason 说明为什么不行。"""

    tier: str = "unsupported"
    quantization: Dict[str, str] = field(default_factory=dict)
    offload: str = "block_level"
    weights_gib: float = 0.0
    min_ram_gib: int = 0
    max_pixels: int = PIXELS_DEFAULT
    available: bool = False
    experimental: bool = False
    reason: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "label": TIER_LABEL.get(self.tier, "不可用"),
            "quantization": dict(self.quantization),
            "offload": self.offload,
            "weights_gib": round(self.weights_gib, 1),
            "min_ram_gib": self.min_ram_gib,
            "max_pixels": self.max_pixels,
            "available": self.available,
            "experimental": self.experimental,
            "reason": self.reason,
            "note": self.note,
        }


def _cap_number(compute_cap: str) -> int:
    """'sm_86' → 86；无法解析返回 0。"""
    text = str(compute_cap or "").strip().lower()
    if not text.startswith("sm_"):
        return 0
    digits = text[3:]
    return int(digits) if digits.isdigit() else 0


def _blocked(reason: str) -> H3QuantPlan:
    return H3QuantPlan(
        tier="unsupported", available=False, reason=reason,
        note="H3 无法在本机运行。", weights_gib=WEIGHT_BASE_GIB,
    )


def plan(vram_gib: float, ram_gib: float, compute_cap: str = "") -> H3QuantPlan:
    """按内存挑最高可用精度，再按显存决定权重是否常驻。

    判定顺序刻意是「内存优先」：
      1. 精度只看内存——因为加权卸载的权重是驻留 CPU 内存的；
      2. 显存只决定两件事：权重能不能常驻（否则按块流式），以及画布上限。
    以前只看显存，才会出现「24GB 卡直接 .to("cuda") 61.7GiB」这种必炸分支。
    """
    try:
        vram = max(float(vram_gib or 0.0), 0.0)
        ram = max(float(ram_gib or 0.0), 0.0)
    except (TypeError, ValueError):
        return _blocked("硬件参数读取失败，无法判断能否运行 H3。")

    if vram < MIN_VRAM_GIB:
        return _blocked(
            f"显存 {vram:.0f}GB 低于 H3 最低要求（{MIN_VRAM_GIB:.0f}GB）。"
            "退一档：用 Wan 2.2 TI2V-5B 引擎（8GB 显存可跑 480p）。"
        )

    # ---- 1. 内存决定精度上限（从高到低挑第一个装得下的）----
    chosen = ""
    for tier in ("full", "int8", "int4"):
        quant = "none" if tier == "full" else tier
        weights = WEIGHT_BASE_GIB * QUANT_FACTOR[quant]
        if ram >= weights + HEADROOM_GIB[quant]:
            chosen = tier
            break

    if not chosen:
        weights = WEIGHT_BASE_GIB * QUANT_FACTOR["int4"]
        need = int(round(weights + HEADROOM_GIB["int4"]))
        return H3QuantPlan(
            tier="int4",
            quantization={"transformer": "int4", "text_encoder": "int4"},
            offload="block_level",
            weights_gib=round(weights, 1),
            min_ram_gib=need,
            max_pixels=_pixels_for("int4", vram),
            available=False,
            experimental=True,
            reason=(
                f"内存不足：即使 int4 量化也要约 {weights:.0f}GiB 权重常驻内存"
                f"（含余量 {need}GiB），当前可用 {ram:.0f}GiB。"
                "按块卸载只省显存、不省内存——这是 H3 的真正门槛，"
                "与显存无关。可选：内存加到 64GB 后重试，或改用 "
                "Wan 2.2 TI2V-5B（8GB 显存即可）。"
            ),
            note=f"int4 需要 {need}GB 内存，当前 {ram:.0f}GB。",
        )

    quant = "none" if chosen == "full" else chosen
    weights = round(WEIGHT_BASE_GIB * QUANT_FACTOR[quant], 1)
    min_ram = int(round(weights + HEADROOM_GIB[quant]))

    # ---- 2. 算力校验（torchao weight-only kernel）----
    cap = _cap_number(compute_cap)
    need_cap = MIN_CAP.get(quant, 0)
    if cap and need_cap and cap < need_cap:
        return _blocked(
            f"{TIER_LABEL[chosen]} 需要 sm_{need_cap} 及以上算力（当前 {compute_cap}）。"
            "请升级到 RTX 30 系及以上显卡，或改用 Wan 2.2 TI2V-5B 引擎。"
        )

    # ---- 3. 显存决定常驻还是流式 ----
    offload = "none" if vram >= weights + 6.0 else "block_level"
    pixels = _pixels_for(chosen, vram)

    note = (
        f"{TIER_LABEL[chosen]}·"
        + ("权重常驻显存（速度优先）" if offload == "none" else "按块卸载（block_level）")
        + f"。权重约 {weights:.0f}GiB，需 {min_ram}GB 内存。"
    )
    if chosen == "int4":
        note += "int4 为实验档，画质未实机标定；建议 512×384 以内、5~8 秒镜头。"
    if pixels < PIXELS_DEFAULT:
        note += f"画布上限 {pixels} 像素，超出的会在归一阶段等比缩。"

    return H3QuantPlan(
        tier=chosen,
        quantization={"transformer": quant, "text_encoder": quant} if quant != "none" else {},
        offload=offload,
        weights_gib=weights,
        min_ram_gib=min_ram,
        max_pixels=pixels,
        available=True,
        experimental=(chosen == "int4"),
        note=note,
    )


def _pixels_for(tier: str, vram: float) -> int:
    """画布上限由显存决定（激活规模），与量化档位无关。"""
    if vram < 10.0:
        return PIXELS_TINY
    if vram < 24.0:
        return PIXELS_LOW_VRAM
    return PIXELS_DEFAULT


def build_quantization_config(quantization: Dict[str, str]) -> dict:
    """把档位说明变成 ``load_components(quantization_config={组件: 配置})``。

    延迟 import：没有 torchao 的环境（比如纯 CPU 开发机）不该因为
    导入本模块就失败。转换器走 diffusers，文本编码器走 transformers——
    两边各有自己的 ``TorchAoConfig``，类型不能混用。
    """
    if not quantization:
        return {}
    from diffusers import TorchAoConfig as DiffusersTorchAo
    from torchao.quantization import Int4WeightOnlyConfig, Int8WeightOnlyConfig
    from transformers import TorchAoConfig as TransformersTorchAo

    ao_types = {"int8": Int8WeightOnlyConfig, "int4": Int4WeightOnlyConfig}
    configs = {}
    for component, kind in quantization.items():
        ao = ao_types.get(kind)
        if ao is None:
            continue
        maker = TransformersTorchAo if component == "text_encoder" else DiffusersTorchAo
        configs[component] = maker(ao())
    return configs


def describe(plan_obj: H3QuantPlan, gpu_name: str = "") -> str:
    """给 UI 用的一句话说明。"""
    if not plan_obj.available:
        return plan_obj.reason
    head = f"{plan_obj.note}"
    if gpu_name:
        head = f"{gpu_name}：{head}"
    return head
