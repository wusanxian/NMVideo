"""硬件探测器：为 Wan 2.2 引擎决定「模型选择 / 量化策略 / offload 模式」

设计目标（FreeVideo 风格）：最低 8GB 显存 + 32GB 内存跑 Wan 2.2。
探测结果决定：
  - 模型：TI2V-5B（bf16 权重 ~10GB，低配首选）或 T2V-A14B（双专家 ~28GB bf16，大内存机器）
  - 量化：40 系+ fp8 / 30 系 int8 weight-only / 其他不量化
  - offload：显存宽裕 model offload，紧张 sequential offload（层流式，8GB 可跑）
"""
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HardwareProfile:
    # 探测结果
    gpu_name: str = "未检测到 NVIDIA 显卡"
    vram_gb: float = 0.0
    compute_cap: str = ""          # sm_86 / sm_89 / sm_120 ...
    ram_gb: float = 0.0
    free_vram_gb: Optional[float] = None
    available_ram_gb: Optional[float] = None
    disk_free_gb: float = 0.0
    torch_cuda_available: bool = False

    # 推荐配置
    device: str = "cpu"
    model_id: str = "ti2v-5b"      # ti2v-5b / t2v-a14b
    quant: str = "none"            # none / int8 / fp8
    offload: str = "none"          # none / model / sequential
    vae_tiling: bool = True
    dtype: str = "fp32"            # fp16 / bf16 / fp32
    est_minutes_per_5s: float = 0.0  # 预估 5 秒 720p 耗时（分钟），0=无法估计
    notes: list = field(default_factory=list)

    def to_dict(self):
        return {
            "gpu_name": self.gpu_name,
            "vram_gb": round(self.vram_gb, 1),
            "compute_cap": self.compute_cap,
            "ram_gb": round(self.ram_gb, 1),
            "free_vram_gb": round(self.free_vram_gb, 1) if self.free_vram_gb is not None else None,
            "available_ram_gb": round(self.available_ram_gb, 1) if self.available_ram_gb is not None else None,
            "disk_free_gb": round(self.disk_free_gb, 1),
            "device": self.device,
            "model_id": self.model_id,
            "quant": self.quant,
            "offload": self.offload,
            "vae_tiling": self.vae_tiling,
            "dtype": self.dtype,
            "est_minutes_per_5s": self.est_minutes_per_5s,
            "notes": self.notes,
        }


def _query_gpu():
    """nvidia-smi 查询显卡名与显存；无卡或命令不存在返回 None。"""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        name, mem = out.stdout.strip().splitlines()[0].split(",")
        return name.strip(), float(mem.strip()) / 1024.0
    except Exception:
        return None


def _query_ram():
    try:
        import psutil
        return psutil.virtual_memory().total / 2**30
    except Exception:
        pass
    # Windows fallback：wmic
    try:
        out = subprocess.run(
            ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line) / 2**30
    except Exception:
        pass
    return 0.0


def _query_disk(path="."):
    try:
        usage = shutil.disk_usage(path)
        return usage.free / 2**30
    except Exception:
        return 0.0


def _compute_cap_from_torch():
    """torch 可用时拿计算架构（sm_86=30系, sm_89=40系, sm_120=50系）"""
    try:
        import torch
        if torch.cuda.is_available():
            major, minor = torch.cuda.get_device_capability(0)
            return f"sm_{major}{minor}"
    except Exception:
        pass
    return ""


def _supports_fp8(compute_cap: str) -> bool:
    """FP8 需要 Ada Lovelace 及以上（sm_89 / sm_120）"""
    try:
        num = int(compute_cap.replace("sm_", ""))
        return num >= 89
    except (ValueError, AttributeError):
        return False


def probe() -> HardwareProfile:
    """探测硬件并给出 Wan 2.2 引擎推荐配置。"""
    p = HardwareProfile()

    gpu = _query_gpu()
    if gpu:
        p.gpu_name, p.vram_gb = gpu

    p.ram_gb = _query_ram()
    try:
        import psutil
        p.available_ram_gb = psutil.virtual_memory().available / 2**30
    except (ImportError, OSError):
        pass
    p.disk_free_gb = _query_disk()
    p.compute_cap = _compute_cap_from_torch()

    # torch 侧确认（nvidia-smi 有卡但驱动/torch 不可用时仍走 CPU 提示）
    try:
        import torch
        p.torch_cuda_available = torch.cuda.is_available()
        if p.torch_cuda_available:
            try:
                p.free_vram_gb = torch.cuda.mem_get_info()[0] / 2**30
            except Exception:
                pass  # 无法测量剩余显存不等于 CUDA 不可用
    except Exception:
        p.torch_cuda_available = False

    usable_ram = max(p.ram_gb - 6.0, 0.0)  # 预留系统占用

    if not p.torch_cuda_available:
        # ---- CPU 路径：能跑，但提前告知速度 ----
        p.device = "cpu"
        p.model_id = "ti2v-5b"
        p.quant = "none"
        p.offload = "none"
        p.dtype = "fp32"
        p.vae_tiling = True
        p.est_minutes_per_5s = 0.0
        p.notes.append("未检测到可用的 NVIDIA 显卡：Wan 2.2 可在 CPU 上运行但极慢（小时级），建议使用 AnimeDiff 引擎。")
        return p

    p.device = "cuda"

    # ---- 模型选择：A14B 需要 bf16 双专家 ~56GB 内存，内存不够一律 TI2V-5B ----
    if usable_ram >= 60 and p.vram_gb >= 20:
        p.model_id = "t2v-a14b"
        p.notes.append("显存与内存充足：使用 T2V-A14B 双专家（画质标杆）。")
    else:
        p.model_id = "ti2v-5b"
        if usable_ram < 20:
            p.notes.append(
                f"可用内存约 {usable_ram:.0f}GB：TI2V-5B bf16 需约 22GB，若加载失败请关闭其他程序或使用量化权重。"
            )

    # ---- 量化策略 ----
    if _supports_fp8(p.compute_cap):
        p.quant = "fp8"
    elif p.compute_cap and p.compute_cap >= "sm_61":  # 30 系及更早 Maxwell+：int8
        p.quant = "int8"
    else:
        p.quant = "none"

    # ---- offload 策略：核心低显存机制 ----
    available_vram = p.free_vram_gb if p.free_vram_gb is not None else p.vram_gb
    if available_vram >= 24:
        p.offload = "none" if p.model_id == "ti2v-5b" else "model"
    elif available_vram >= 12:
        p.offload = "model"      # 层组卸载，速度损失小
    else:
        p.offload = "sequential"  # 逐层流式卸载，8GB 可跑，速度换显存

    # ---- dtype ----
    p.dtype = "bf16" if p.model_id == "ti2v-5b" else "bf16"

    # ---- 耗时预估（粗粒度，按 720p/81帧/30步 折算） ----
    # 基准：4090 24GB 无 offload 约 6 分钟；按显存比与 offload 惩罚估算
    base = 6.0
    vram_factor = max(24.0 / max(p.vram_gb, 1.0), 1.0)
    offload_penalty = {"none": 1.0, "model": 2.0, "sequential": 4.0}[p.offload]
    model_factor = 1.0 if p.model_id == "ti2v-5b" else 2.5
    quant_factor = 0.75 if p.quant in ("fp8", "int8") else 1.0
    p.est_minutes_per_5s = round(base * vram_factor * offload_penalty * model_factor * quant_factor, 1)

    if p.vram_gb <= 8:
        p.notes.append(
            f"8GB 级显存：sequential offload + {p.quant} 量化，5 秒 720p 预计 {p.est_minutes_per_5s:.0f} 分钟左右；"
            "赶时间可选「轻量」档（480p 快数倍）。"
        )
    return p


if __name__ == "__main__":
    import json
    print(json.dumps(probe().to_dict(), ensure_ascii=False, indent=2))
