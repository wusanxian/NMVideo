# -*- coding: utf-8 -*-
"""
AI 视频工坊 Windows 启动器（打包进 exe 的入口）

双击 exe 后的全自动流程：
  1. 解压载荷（后端源码 + 前端产物）到 runtime 目录
  2. 探测硬件（NVIDIA 显卡/显存/内存/磁盘，零第三方依赖）
  3. 按硬件矩阵决策：模型选型 + torch 版本 + 量化/卸载策略
  4. 准备 Python 环境：优先系统 Python(>=3.10) 建 venv；没有则下载 embeddable Python
  5. 安装依赖：按显卡装 torch(CUDA/CPU 版) + requirements.txt
  6. 按决策下载模型（hf-mirror 镜像）
  7. 启动 uvicorn 服务并打开浏览器

命令行参数（可选，全部有自动默认）：
  --model auto|h3|wan-ti2v|wan-a14b|anime  模型选型（默认 auto 按硬件；h3 = 原生兜底通道，正路走 ComfyUI）
  --torch auto|cuda|cpu                 torch 版本（默认 auto 按显卡）
  --port N                              服务端口（默认 8000）
  --yes                                 跳过倒计时确认直接执行
  --no-download                         跳过模型下载（只准备环境+启动）
  --reinstall                           重建 venv 重装所有依赖
  --no-browser                          不起系统浏览器（Electron 桌面端模式）
  --plain                               无 ANSI 颜色输出（管道/日志模式）
  --port-file PATH                      就绪后把最终端口写入文件（桌面端读取）

结构化输出（桌面端解析，--plain 时同样生效）：
  [AIVIDEO] STATE=<preparing|python|torch|deps|models|starting|ready|error>
  [AIVIDEO] PORT=<n>
"""
import ctypes
import ctypes.wintypes as wt
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


def _setup_output_encoding():
    """输出编码统一：

    - 管道模式（Electron 桌面端 / 重定向日志）：stdout 强制 UTF-8。
      桌面壳 main.js 与日志文件都按 UTF-8 解码，Python 管道默认却用本地
      编码（中文 Windows = GBK），会导致中文全部变成乱码符号。
    - 真实控制台（用户双击 exe）：不动，Python 对控制台走 WriteConsoleW
      天然按 Unicode 输出，cmd 里中文正常。
    - 同时给子进程（pip / download_models.py / uvicorn）设 UTF-8 环境，
      它们的输出会被透传到同一个管道。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None or stream.isatty():
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


_setup_output_encoding()

# ---------------- 常量 ----------------
APP_NAME = "NMVideo"
# 数据目录名沿用历史值，**不要跟着 APP_NAME 改**：0.1.2 及更早版本把 venv/模型/数据库
# 落在 %LOCALAPPDATA%\AI视频工坊\runtime，改名等于让老用户丢掉已下载的模型和作品库。
DATA_DIR_NAME = "AI视频工坊"
RUNTIME_DIR_NAME = "runtime"
EMBED_PY_VER = "3.12.8"
EMBED_PY_URL = f"https://mirrors.huaweicloud.com/python/{EMBED_PY_VER}/python-{EMBED_PY_VER}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
PIP_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
HF_ENDPOINT = "https://hf-mirror.com"
TORCH_INDEX = {
    "cuda": "https://download.pytorch.org/whl/cu124/",
    "cpu": "https://download.pytorch.org/whl/cpu/",
}
DEFAULT_PORT = 8000
MIN_PY_VER = (3, 10)

# H3 有两条通道，门槛差三倍，千万不能混算（数字与 backend/app/h3_freevideo.py、
# backend/app/engines/h3_quant.py 同口径，改一处要三处同步）：
#   - 插件通道（正路，引擎 h3）：ComfyUI + FreeVideo 节点，权重按 50 个 transformer
#     块在「显存 → 内存 → 磁盘」之间摆放，装不下的块每步回读磁盘，文本编码器
#     独立进程跑完即退 → 8GB 显存 + 32GB 内存就能跑，只是慢。四个素材包
#     49.50GiB，含下载余量要 57.5GiB 磁盘。
#     （2026-11-01 真机校正：原来按 model_files.json 的 bf16 分片记成 92.34GiB，
#      实际跑的是 int8-convrot 预量化包 21.35GiB，原版 bf16 transformer 从不下载，
#      见 backend/app/h3_freevideo.py 的模块说明。用旧值会把 97GB 可用盘判死。）
#   - 原生通道（兜底，引擎 h3_native）：本进程 diffusers 加载 bf16 原始权重
#     123.8GiB，内存决定档位，52GB 内存起步、139GB 磁盘。
# 早先只按原生口径判，于是 8GB/32GB 的机器被告知「跑不了 H3、给你下 Wan」——
# 那 30GB 白下了，这是 0.1.9 真机跑出来的坑。
H3_PLUGIN_MIN_VRAM = 8.0
# 内存线按 32GB 标定（机制 16GB 就能起，但回读磁盘的块太多，慢到不可用）
H3_PLUGIN_MIN_RAM = 32.0
H3_PLUGIN_TOTAL_GIB = 49.5
H3_PLUGIN_DISK_GIB = 57.5

# 门槛比较的容差（GB）。显卡标称 8GB 实机是 8188MiB ≈ 7.996GB，
# nvidia-smi 报 memory.total=8188 → /1024 后严格 `>= 8.0` 为 False，
# 于是所有「8GB 卡」都被判成显存不足。真机 4060 上直接暴露：页面说 8GB 能跑，
# 启动器却打印「显存偏小，强制 int8」。留半 GB 容差，别和标称值较劲。
VRAM_TOLERANCE_GB = 0.5

# 内存同理：32GB 机器实际报 31.96GB（固件预留 + 核显共享显存），
# 严格 `>= 32` 会把「正好 32GB」这类目标配置挡在门外。
RAM_TOLERANCE_GB = 0.6

# 模型磁盘需求（GB，含余量）
DISK_NEED = {
    "h3": H3_PLUGIN_DISK_GIB,  # 插件通道（正路）
    "h3_native": 150.0,        # 原生兜底：底模 78GB + Qwen3-VL 文本编码器 62GB，留 10GB 余量
    "wan-a14b": 135.0,
    "wan-ti2v": 42.0,
    "anime": 14.0,
}

# 全局运行选项（argparse 后填充）
OPTS = {"plain": False}


def emit_state(state: str):
    """结构化状态行：桌面端壳解析它驱动进度/状态机"""
    print(f"[AIVIDEO] STATE={state}", flush=True)


def emit_port(port: int):
    print(f"[AIVIDEO] PORT={port}", flush=True)


# ---------------- 控制台 UI ----------------
# Windows 默认 cmd 是 GBK 代码页：▶ ✔ ⚠ ╔ 等符号不在码表里，print 会直接
# UnicodeEncodeError 崩溃。所有输出统一走 _safe() 降级为 GBK 安全字符。
_SAFE_TABLE = str.maketrans({
    "\u25b6": ">", "\u2714": "[OK]", "\u2718": "[X]", "\u26a0": "[!]",
    "\u2554": "+", "\u2550": "=", "\u2557": "+", "\u255a": "+",
    "\u255d": "+", "\u2551": "|", "\u2500": "-",
})


def _safe(s: str) -> str:
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        s = s.translate(_SAFE_TABLE)
        try:
            return s.encode(enc, "replace").decode(enc, "replace")
        except (UnicodeEncodeError, LookupError):
            return s.encode("ascii", "replace").decode("ascii", "replace")


def emit(s: str):
    try:
        print(s, flush=True)
    except UnicodeEncodeError:
        print(_safe(s), flush=True)


class UI:
    @staticmethod
    def _enable_vt():
        if os.name == "nt":
            try:
                k = ctypes.windll.kernel32
                k.SetConsoleMode(k.GetStdHandle(-11), 7)
            except Exception:
                pass

    def __init__(self):
        self._enable_vt()

    @staticmethod
    def _c(code, text):
        if OPTS["plain"]:
            return text
        return f"\033[{code}m{text}\033[0m"

    def banner(self):
        emit(self._c("1;35", f"""
+==========================================+
        {APP_NAME} - 本地 AI 视频生成器
   Wan 2.2 / AnimeDiff - 硬件自适应 - 一键
+==========================================+"""))

    def step(self, msg):
        emit("\n" + self._c("1;36", "> " + msg))

    def ok(self, msg):
        emit(self._c("1;32", "  [OK] " + msg))

    def warn(self, msg):
        emit(self._c("1;33", "  [!] " + msg))

    def info(self, msg):
        emit(self._c("2", "  " + msg))

    def fatal(self, msg):
        emit(self._c("1;31", "\n[X] " + msg))
        sys.exit(1)

    def ask_continue(self, seconds=6):
        emit(self._c("1;33", f"\n  按回车立即开始，{seconds} 秒后自动开始；按 Q + 回车退出"))
        import threading
        got = threading.Event()
        ans = []

        def _read():
            try:
                ans.append(input().strip().lower())
            except Exception:
                pass
            got.set()

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        got.wait(seconds)
        if got.is_set() and ans and ans[0] in ("q", "quit", "exit"):
            emit("已退出。")
            sys.exit(0)


ui = UI()


# ---------------- 硬件探测（纯标准库） ----------------
@dataclass
class HWInfo:
    gpus: list = field(default_factory=list)   # [(name, vram_gb)]
    cpu: str = ""
    ram_total_gb: float = 0.0
    ram_avail_gb: float = 0.0
    disk_free_gb: float = 0.0
    # 显存是否真的读到了。False = 知道有 N 卡但读不出容量（nvidia-smi 与 torch
    # 都失灵时的兜底），此时绝不能当成「0GB 显存」或「没有显卡」来判断。
    vram_known: bool = True
    gpu_source: str = ""       # 探测来源：smi / torch / wmi，出问题时报给用户

    @property
    def max_vram_gb(self) -> float:
        return max((v for _, v in self.gpus), default=0.0)


def _global_memory():
    class MEMSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    m = MEMSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys / 2**30, m.ullAvailPhys / 2**30


def probe_hardware(rt: Path | None = None) -> HWInfo:
    hw = HWInfo()
    hw.cpu = platform.processor() or platform.machine()
    try:
        hw.ram_total_gb, hw.ram_avail_gb = _global_memory()
    except Exception:
        pass
    try:
        # 必须按 runtime 所在盘算：装 D 盘、runtime 退到 %LOCALAPPDATA%（C 盘）时，
        # 拿 cwd 的盘去判会得出完全错误的「磁盘够不够」。
        anchor = (rt or Path.cwd()).anchor or "C:\\"
        hw.disk_free_gb = shutil.disk_usage(anchor).free / 2**30
    except Exception:
        pass
    gpus, source, known = _probe_gpus(rt)
    hw.gpus = gpus
    hw.gpu_source = source
    hw.vram_known = known
    return hw


def _probe_gpus(rt: Path | None = None):
    """三级探测显卡：nvidia-smi → torch → 系统显卡列表。

    为什么不能只靠 nvidia-smi：真机上遇到过 `Failed to initialize NVML`，
    nvidia-smi 直接失效，但 CUDA 完全可用（torch 报 8GB/sm_89、矩阵乘正常）。
    只认 nvidia-smi 会把这种机器判成「没有显卡」→ 去装 CPU 版 torch，
    把已经装好的 CUDA 环境覆盖掉。探测工具坏了不等于显卡坏了。

    返回 (gpus, source, vram_known)。
    """
    # ① nvidia-smi：最快最准，能拿到显存容量
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi:
        try:
            done = subprocess.run(
                [nvsmi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            gpus = []
            for line in (done.stdout or "").strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        gpus.append((parts[0], int(parts[1]) / 1024.0))
                    except ValueError:
                        pass
            if gpus:
                return gpus, "smi", True
        except Exception:
            pass

    # ② torch：用 runtime 里的 venv 问一次 torch（已装 torch 时才可能成功）
    if rt is not None:
        py = rt / "venv" / "Scripts" / "python.exe"
        if py.exists():
            code = (
                "import torch;"
                "print(torch.cuda.is_available());"
                "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '');"
                "print(int(torch.cuda.get_device_properties(0).total_memory) if torch.cuda.is_available() else 0)"
            )
            try:
                done = subprocess.run([str(py), "-c", code],
                                      capture_output=True, text=True, timeout=90)
                lines = (done.stdout or "").strip().splitlines()
                if len(lines) >= 3 and lines[0].strip() == "True":
                    gpus = [(lines[1].strip() or "NVIDIA GPU", int(lines[2]) / 2**30)]
                    return gpus, "torch", True
            except Exception:
                pass

    # ③ 系统显卡列表：只知道「有张 N 卡」，拿不到显存（WMI 的 AdapterRAM 是 32 位，不准）
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Where-Object { $_.Name -match 'NVIDIA' } | "
             "Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        names = [n.strip() for n in (done.stdout or "").splitlines() if n.strip()]
        if names:
            return [(names[0], 0.0)], "wmi", False
    except Exception:
        pass
    return [], "", True


def show_hw_report(hw: HWInfo):
    ui.step("硬件探测结果")
    print(f"  CPU   : {hw.cpu}")
    print(f"  内存   : {hw.ram_total_gb:.0f} GB（当前可用 {hw.ram_avail_gb:.0f} GB）")
    print(f"  磁盘   : 可用 {hw.disk_free_gb:.0f} GB")
    if hw.gpus:
        for name, vram in hw.gpus:
            if hw.vram_known:
                print(f"  GPU   : {name} · {vram:.0f} GB 显存")
            else:
                print(f"  GPU   : {name} · 显存读取失败（探测工具异常，显卡本身可用）")
    else:
        print("  GPU   : 未检测到 NVIDIA 显卡（将走 CPU 路径）")


# ---------------- 决策矩阵 ----------------
@dataclass
class Plan:
    model: str          # h3 | h3_native | wan-a14b | wan-ti2v | anime
    torch: str          # cuda | cpu
    quantize: str       # auto | int8 | fp8 | none
    offload: str        # sequential | model | none
    reason: str = ""


def choose_plan(hw: HWInfo) -> Plan:
    if hw.gpus:
        vram = hw.max_vram_gb
        # 显存读不出来（nvidia-smi 挂了、torch 还没装、只有系统显卡列表）：
        # 明确知道有 N 卡，但不知道容量 → 选最省显存的档，且保持 CUDA 版 torch。
        # 绝不能掉进下面「显存偏小 / 没显卡」的判断，那会让用户被装 CPU 版 torch。
        if not hw.vram_known:
            return Plan("wan-ti2v", "cuda", "auto", "sequential",
                        f"检测到 {hw.gpus[0][0]}，但显存容量读不出来"
                        "（显卡探测工具异常，显卡本身可用）→ 按最省显存的 TI2V-5B + "
                        "逐层卸载处理，并保留 CUDA 版 PyTorch。"
                        "若出片报显存不足，请先重装/重启显卡驱动后重新检测")
        # 比较用「带容差的显存」：8GB 卡实机是 7.996GB（8188MiB），
        # 严格 >= 8 会把它判成显存不足。显示仍用真实值，别骗用户。
        veff = vram + VRAM_TOLERANCE_GB
        # 正路：H3 通道（ComfyUI 工作流）。门槛是 8GB 显存 /
        # 32GB 内存 / 57.5GB 磁盘 —— 权重按块在三级存储间摆放，装不下的块回读
        # 磁盘，所以内存决定的是「快慢」而不是「能不能起」：内存低于 32GB 时
        # 回读的块太多，一段 10 秒要按小时算，不当作可用档对外承诺。
        # 素材由 ComfyUI 侧下载（谁跑推理谁放权重），本程序不重复下一份。
        if (veff >= H3_PLUGIN_MIN_VRAM and hw.ram_total_gb + RAM_TOLERANCE_GB >= H3_PLUGIN_MIN_RAM
                and hw.disk_free_gb >= H3_PLUGIN_DISK_GIB):
            return Plan("h3", "cuda", "auto", "none",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存 + "
                        f"{hw.disk_free_gb:.0f}GB 磁盘：可跑 H3（分块流式，慢但画质最好）。"
                        f"素材 {H3_PLUGIN_TOTAL_GIB:.0f}GB 在 ComfyUI 侧装一次即可，"
                        "本程序不重复下载")
        # 显存内存都够、只差磁盘：说清差多少，不要偷偷换成 Wan（用户会白下 30GB）
        if veff >= H3_PLUGIN_MIN_VRAM and hw.ram_total_gb + RAM_TOLERANCE_GB >= H3_PLUGIN_MIN_RAM:
            short = H3_PLUGIN_DISK_GIB - hw.disk_free_gb
            return Plan("wan-ti2v", "cuda", "auto", "sequential",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存够跑 H3，"
                        f"但磁盘只剩 {hw.disk_free_gb:.0f}GB，素材要 {H3_PLUGIN_DISK_GIB:.0f}GB，"
                        f"还差 {short:.0f}GB → 先腾空间；急用可在「设置 → 模型」里"
                        f"自己下低显存档（约 32GB，不自动下载）")
        # 原生兜底（进程内 diffusers，123.8GiB 权重）：内存决定档位，52GB 起步。
        # 只有内存极大、磁盘也够的机器才可能走到这里（插件通道磁盘要求更低，
        # 正常早就命中上面了），保留它是为了「有明确理由不要插件通道」的场景。
        if veff >= 10 and hw.ram_total_gb >= 52 and hw.disk_free_gb >= DISK_NEED["h3_native"]:
            return Plan("h3_native", "cuda", "auto", "sequential",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存 + "
                        f"{hw.disk_free_gb:.0f}GB 磁盘：可跑 H3 原生推理（8 步蒸馏）。"
                        "注意这是兜底通道：正路是 ComfyUI 通道，"
                        "需要与 ComfyUI 同参同结果时请走插件通道")
        if veff >= 22 and hw.ram_total_gb >= 60:
            return Plan("wan-a14b", "cuda", "auto", "sequential",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存，可上 Wan2.2-A14B 画质标杆")
        if veff >= 8 and hw.ram_total_gb >= 24:
            return Plan("wan-ti2v", "cuda", "auto", "sequential",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存，TI2V-5B + int8 量化 + 逐层卸载")
        if vram > 0:
            return Plan("wan-ti2v", "cuda", "int8", "sequential",
                        f"{vram:.0f}GB 显存偏小，强制 int8 + 逐层卸载，速度会明显下降")
    # 无 NVIDIA 显卡
    if hw.ram_total_gb >= 24:
        return Plan("anime", "cpu", "none", "none",
                    "未检测到 NVIDIA 显卡，使用 AnimeDiff 引擎（CPU 可跑，分钟级出片）")
    return Plan("", "cpu", "none", "none",
                f"内存仅 {hw.ram_total_gb:.0f}GB，低于 24GB 最低要求")


PLAN_LABEL = {
    "h3": f"画质优先 · H3 通道（ComfyUI 工作流 · 分块流式 · 素材约 {H3_PLUGIN_TOTAL_GIB:.0f}GB，ComfyUI 侧准备）",
    "h3_native": "原生兜底 · H3 进程内推理（diffusers · bf16 约 124GiB · 内存决定档位）",
    "wan-a14b": "Wan 2.2 T2V-A14B（双专家 MoE · 电影级画质 · 约 119GB 模型）",
    "wan-ti2v": "Wan 2.2 TI2V-5B（5B 统一模型 · 8GB 显存甜点位 · 约 32GB 模型）",
    "anime": "AnimeDiff 动漫引擎（dreamlike-anime + 运动模块 · 约 5GB 模型）",
}


def show_plan(plan: Plan, hw: HWInfo):
    ui.step("运行方案（按硬件自动决策）")
    print(f"  模型   : {PLAN_LABEL.get(plan.model, plan.model)}")
    print(f"  决策依据: {plan.reason}")
    if plan.torch == "cuda":
        print("  PyTorch: CUDA 12.4 版（约 2.5GB，下载耗时较长）")
    else:
        print("  PyTorch: CPU 版（约 200MB）")
    if plan.model == "h3":
        print(f"  素材   : 约 {H3_PLUGIN_TOTAL_GIB:.0f}GB，由 ComfyUI 侧准备（本程序不重复下）")
        print("  权重摆放: 插件分块流式（显存→内存→磁盘，装不下的块回读磁盘）")
    elif plan.model in ("wan-ti2v", "wan-a14b"):
        gb = 32 if plan.model == "wan-ti2v" else 119
        print(f"  权重   : 约 {gb}GB，默认不下载——出片前在「设置 → 模型」里自己点")
    elif plan.quantize in ("int8", "fp8", "auto"):
        q = {"auto": "按显卡自动（40 系 fp8 / 30 系 int8）", "int8": "int8 权重量化"}[plan.quantize]
        print(f"  量化   : {q}")
    if plan.offload == "sequential":
        print("  显存   : 逐层 CPU 卸载（速度换显存，8GB 卡也能跑）")
    need = DISK_NEED.get(plan.model, 0)
    if need and hw.disk_free_gb < need:
        ui.warn(f"磁盘可用 {hw.disk_free_gb:.0f}GB，方案需要约 {need:.0f}GB，空间不足可能失败！")
    else:
        ui.info(f"磁盘需求约 {need:.0f}GB，当前可用 {hw.disk_free_gb:.0f}GB ✓")


# ---------------- payload 同步 ----------------
def payload_root() -> Path:
    """PyInstaller onefile 运行时 payload 在 sys._MEIPASS；开发模式取项目目录"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def real_exe_path() -> Path:
    """用户实际双击的 exe 路径。

    PyInstaller onefile 运行时 sys.executable 指向 %TEMP%\\_MEIxxxxx\\ 下的临时
    bootloader，不是用户双击的文件；GetModuleFileNameW(None) 拿的才是进程真实镜像路径。
    """
    if os.name == "nt":
        try:
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.kernel32.GetModuleFileNameW(None, buf, 260)
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path(sys.argv[0]).resolve()


def runtime_dir() -> Path:
    """持久化 runtime 目录：优先 exe 同级，失败退 %LOCALAPPDATA%"""
    try:
        base = real_exe_path().resolve().parent
        base.joinpath(".write_test").write_text("")
        base.joinpath(".write_test").unlink()
        return base / RUNTIME_DIR_NAME
    except Exception:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(local) / DATA_DIR_NAME / RUNTIME_DIR_NAME


def sync_payload(dest: Path):
    src = payload_root() / "payload"
    if not src.exists():
        # 开发模式：直接拿项目里的 backend/frontend
        proj = Path(__file__).resolve().parent.parent
        for sub in ("backend", "frontend"):
            if (proj / sub).exists():
                _copy_tree(proj / sub, dest / sub)
        return
    _copy_tree(src, dest)


def _copy_tree(src: Path, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    skip_names = {"models", "outputs", ".venv", "__pycache__", "aivideo.db",
                  "aivideo.db-wal", "aivideo.db-shm", "settings.json", ".cache"}
    for item in src.iterdir():
        if item.name in skip_names:
            continue
        target = dst / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            shutil.copy2(item, target)


# ---------------- Python 环境 ----------------
def find_system_python() -> str | None:
    for name in ("python", "python3", "python3.12", "python3.11", "python3.10"):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "-c", "import sys;print(sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
            ver = eval(out)
            if tuple(ver) >= MIN_PY_VER:
                return exe
        except Exception:
            continue
    return None


def _download(url: str, dest: Path, timeout=120):
    ui.info(f"下载 {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = 0
    while True:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, length=1 << 20)
            if dest.stat().st_size > 0:
                return
        except Exception as e:
            ui.warn(f"下载失败（{e}），重试中…")
            time.sleep(3)
        last += 1
        if last >= 5:
            ui.fatal(f"无法下载 {url}，请检查网络后重试")


def setup_embed_python(rt: Path) -> Path:
    """下载 embeddable Python 并装好 pip（用户机器没有 Python 时的兜底）"""
    py_dir = rt / "python-embed"
    py_exe = py_dir / "python.exe"
    if not py_exe.exists():
        ui.step("未检测到系统 Python，下载便携 Python（约 12MB）")
        zip_path = rt / "python-embed.zip"
        _download(EMBED_PY_URL, zip_path)
        py_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(py_dir)
        zip_path.unlink()
        # 取消 ._pth 中 import site 的注释，让 site-packages 生效
        for pth in py_dir.glob("python*._pth"):
            text = pth.read_text(encoding="utf-8")
            if "#import site" in text:
                pth.write_text(text.replace("#import site", "import site"), encoding="utf-8")
        # 装 pip
        gp = rt / "get-pip.py"
        _download(GET_PIP_URL, gp)
        subprocess.run([str(py_exe), str(gp), "-i", PIP_INDEX, "--no-warn-script-location"],
                       check=True)
        gp.unlink()
        ui.ok(f"便携 Python {EMBED_PY_VER} 就绪")
    return py_exe


def ensure_python_env(rt: Path, reinstall: bool = False):
    """返回 (venv_python, pip_cmd_prefix)"""
    venv_dir = rt / "venv"
    if reinstall and venv_dir.exists():
        shutil.rmtree(venv_dir, ignore_errors=True)
    sys_py = find_system_python()
    if sys_py:
        if not venv_dir.exists():
            ui.step(f"使用系统 Python 创建虚拟环境（{sys_py}）")
            subprocess.run([sys_py, "-m", "venv", str(venv_dir)], check=True)
        return venv_dir / "Scripts" / "python.exe", "venv"
    py_exe = setup_embed_python(rt)
    return py_exe, "embed"


def deps_fingerprint(rt: Path, torch_ver: str, py: Path) -> str:
    """依赖指纹：requirements.txt 内容 + torch 档位 + 解释器版本。

    任一项变了就重装（换了 cu124/cpu、改了依赖清单、venv 换了 Python），
    三项都没变就该跳过 pip —— 每次都重跑只是白等。
    """
    import hashlib
    h = hashlib.sha256()
    req = rt / "backend" / "requirements.txt"
    try:
        h.update(req.read_bytes())
    except Exception:
        h.update(b"<no-requirements>")
    h.update(torch_ver.encode("utf-8"))
    h.update(str(py).encode("utf-8"))
    return h.hexdigest()[:16]


def pip_install(py: Path, args: list, desc: str):
    """args: pip install 后的参数（可含 --index-url；已指定源时不再叠加默认镜像）"""
    ui.step(desc)
    # --no-warn-script-location：内嵌 Python 的 Scripts 目录不进 PATH 是刻意的隔离设计，
    # isympy.exe 之类的脚本位置警告对用户只是噪音
    cmd = [str(py), "-m", "pip", "install", "--no-warn-script-location", *args]
    if not any(a == "-i" or a.startswith("--index-url") for a in args):
        cmd += ["-i", PIP_INDEX]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        ui.fatal(f"安装失败: {' '.join(args)}\n请检查网络后重新运行 exe（已装部分会保留）")


# ---------------- 模型下载 ----------------
def ensure_models(rt: Path, py: Path, plan: Plan, hw: HWInfo, explicit: bool = False):
    """只替用户准备「绕不开的那一份」，其余一律等用户自己点。

    Wan 2.2 权重是 32GB / 119GB 量级，值不值得占这块磁盘是用户的决定；
    自动决策可以推荐它，但不能顺手把几十 GB 拉下来——磁盘一紧，用户还得
    自己找是哪个目录吃的。所以：
      - ``auto`` 推出 Wan 时：只提示、不下载，去界面里下；
      - 用户显式 ``--model wan-*``：那是自己点名要的，照下。
    """
    if plan.model == "h3":
        # 权重由 ComfyUI 侧自己管：实际跑的是那边准备好的预量化包
        # （prepared/edge-*）与 models/vdn、models/encoder，不在 ComfyUI 标准 models 目录。
        # NMVideo 再下一份 50GB 只会白占磁盘、还和插件版本对不上。
        ui.step("生成素材：由 ComfyUI 侧准备")
        ui.info(f"H3 素材共 {H3_PLUGIN_TOTAL_GIB:.0f}GB，在 ComfyUI 侧装一次即可")
        ui.info("本程序不重复下载；装好后在「ComfyUI 对接」里填地址即可出片")
        return
    if plan.model in ("wan-ti2v", "wan-a14b") and not explicit:
        gb = 32 if plan.model == "wan-ti2v" else 119
        ui.step("模型权重：这一步留给你自己决定")
        ui.info(f"{PLAN_LABEL[plan.model].split('（')[0]} 的权重约 {gb}GB，本程序不自动下载")
        ui.info("要出片：打开软件 →「设置 → 模型」点「下载」（支持断点续传）")
        ui.info(f"想现在就下：重新运行本 exe 并加 --model {plan.model}")
        return
    if plan.model == "h3_native":
        repo_args = ["--h3"]  # 原生权重：走 HF 缓存，约 124GiB，耗时长
    elif plan.model == "anime":
        repo_args = []  # AnimeDiff 是 download_models.py 的默认行为（不带参数）
    elif plan.model == "wan-ti2v":
        repo_args = ["--wan-ti2v-5b"]
    elif plan.model == "wan-a14b":
        repo_args = ["--wan-a14b"]
    else:
        return
    ui.step(f"下载模型：{PLAN_LABEL[plan.model]}")
    env = dict(os.environ, HF_ENDPOINT=HF_ENDPOINT)
    script = rt / "backend" / "scripts" / "download_models.py"
    proc = subprocess.run([str(py), str(script), *repo_args], env=env)
    if proc.returncode != 0:
        ui.warn("模型下载未完成，可重新运行 exe 续传/重试")


# ---------------- 启动服务 ----------------
def wait_health(port: int, timeout: float = 90.0) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int) -> int:
    """端口被占用时自动顺延（用户机器上可能有别的服务占 8000）"""
    if port_free(preferred):
        return preferred
    for p in range(preferred + 1, preferred + 20):
        if port_free(p):
            ui.warn(f"端口 {preferred} 被占用，改用 {p}")
            return p
    ui.fatal(f"端口 {preferred}-{preferred + 19} 全部被占用，请用 --port 指定其他端口")


def launch(rt: Path, py: Path, port: int, no_browser: bool = False, port_file: str = ""):
    port = pick_port(port)
    emit_state("starting")
    ui.step("启动服务…")
    env = dict(os.environ, AIVIDEO_PORT=str(port))
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(rt / "backend"), env=env,
    )
    if not wait_health(port):
        emit_state("error")
        ui.fatal("服务启动超时，请把上方错误信息反馈给开发者")
    url = f"http://127.0.0.1:{port}"
    ui.ok(f"服务已启动: {url}")
    if port_file:
        try:
            Path(port_file).write_text(str(port), encoding="utf-8")
        except Exception:
            pass
    emit_state("ready")
    emit_port(port)
    if not no_browser:
        webbrowser.open(url)
    else:
        ui.info("桌面端模式：不打开系统浏览器，由 Electron 壳展示界面。")
    ui.info("关闭此窗口即停止服务。生成视频请访问左侧「生成」页。")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


# ---------------- 主流程 ----------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Windows 启动器")
    parser.add_argument("--model", default="auto",
                        choices=["auto", "h3", "h3_native", "wan-ti2v", "wan-a14b", "anime"])
    parser.add_argument("--torch", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--reinstall", action="store_true")
    parser.add_argument("--no-browser", action="store_true",
                        help="不起系统浏览器（Electron 桌面端模式）")
    parser.add_argument("--plain", action="store_true",
                        help="无 ANSI 颜色输出（管道/日志模式）")
    parser.add_argument("--port-file", default="",
                        help="就绪后把最终端口写入该文件（桌面端读取）")
    args = parser.parse_args()

    OPTS["plain"] = args.plain
    emit_state("preparing")

    ui.banner()
    rt = runtime_dir()
    ui.info(f"运行目录: {rt}")

    ui.step("准备程序文件")
    sync_payload(rt)
    ui.ok("程序文件就绪")

    hw = probe_hardware(rt)
    show_hw_report(hw)
    plan = choose_plan(hw)
    if args.model != "auto":
        plan.model = args.model
        plan.reason = f"用户手动指定（--model {args.model}）"
    if args.torch != "auto":
        plan.torch = args.torch
    if not plan.model:
        emit_state("error")
        ui.fatal(f"硬件不满足最低要求：{plan.reason}")
    show_plan(plan, hw)

    if not args.yes:
        ui.ask_continue()

    emit_state("python")
    py, kind = ensure_python_env(rt, reinstall=args.reinstall)
    ui.ok(f"Python 环境就绪（{kind}）")

    # torch 单独装（按显卡选版本）；官方源不稳时用阿里云 pypi 兜底
    torch_ver = plan.torch if args.torch == "auto" else args.torch
    torch_args = ["torch", "--index-url", TORCH_INDEX[torch_ver]]
    if torch_ver == "cpu":
        torch_args += ["--extra-index-url", PIP_INDEX]
    # 依赖指纹命中就跳过两次 pip：pip 虽然幂等，但每次启动都要跑 15–60 秒的
    # 依赖解析，用户看到的就是「明明装好了，每次开软件还要等一遍」。
    # 戳记放在 venv/Scripts 里：venv 被删/重建时戳记跟着没了，不会误跳。
    stamp = py.parent / ".deps-ok"
    want = deps_fingerprint(rt, torch_ver, py)
    deps_ready = False
    if not args.reinstall:
        try:
            deps_ready = stamp.read_text(encoding="utf-8").strip() == want
        except Exception:
            deps_ready = False

    emit_state("torch")
    if deps_ready:
        emit_state("deps")
        ui.ok("依赖已就绪，跳过安装（要强制重装加 --reinstall）")
    else:
        pip_install(py, torch_args,
                    f"安装 PyTorch（{torch_ver.upper()} 版，约 {'2.5GB' if torch_ver == 'cuda' else '200MB'}）")
        ui.ok("PyTorch 安装完成")

        emit_state("deps")
        pip_install(py, ["-r", str(rt / "backend" / "requirements.txt")],
                    "安装其余依赖（FastAPI / diffusers 等，约 500MB）")
        ui.ok("依赖安装完成")
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(want, encoding="utf-8")
        except Exception:
            pass

    if not args.no_download:
        emit_state("models")
        ensure_models(rt, py, plan, hw, explicit=args.model != "auto")
    else:
        ui.warn("已跳过模型下载（--no-download）")

    launch(rt, py, args.port, no_browser=args.no_browser, port_file=args.port_file)


if __name__ == "__main__":
    main()
