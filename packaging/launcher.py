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
  --model auto|h3|wan-ti2v|wan-a14b|anime  模型选型（默认 auto 按硬件）
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

# 模型磁盘需求（GB，含余量）
DISK_NEED = {
    "h3": 150.0,          # 底模 78GB + Qwen3-VL 文本编码器 62GB，留 10GB 余量
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


def probe_hardware() -> HWInfo:
    hw = HWInfo()
    hw.cpu = platform.processor() or platform.machine()
    try:
        hw.ram_total_gb, hw.ram_avail_gb = _global_memory()
    except Exception:
        pass
    try:
        hw.disk_free_gb = shutil.disk_usage(Path.cwd().anchor or "C:\\").free / 2**30
    except Exception:
        pass
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi:
        try:
            out = subprocess.run(
                [nvsmi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        hw.gpus.append((parts[0], int(parts[1]) / 1024.0))
                    except ValueError:
                        pass
        except Exception:
            pass
    return hw


def show_hw_report(hw: HWInfo):
    ui.step("硬件探测结果")
    print(f"  CPU   : {hw.cpu}")
    print(f"  内存   : {hw.ram_total_gb:.0f} GB（当前可用 {hw.ram_avail_gb:.0f} GB）")
    print(f"  磁盘   : 可用 {hw.disk_free_gb:.0f} GB")
    if hw.gpus:
        for name, vram in hw.gpus:
            print(f"  GPU   : {name} · {vram:.0f} GB 显存")
    else:
        print("  GPU   : 未检测到 NVIDIA 显卡（将走 CPU 路径）")


# ---------------- 决策矩阵 ----------------
@dataclass
class Plan:
    model: str          # wan-a14b | wan-ti2v | anime
    torch: str          # cuda | cpu
    quantize: str       # auto | int8 | fp8 | none
    offload: str        # sequential | model | none
    reason: str = ""


def choose_plan(hw: HWInfo) -> Plan:
    if hw.gpus:
        vram = hw.max_vram_gb
        # MiniMax H3 原生推理（VDN-H3 8 步蒸馏）：模型约 139GB。
        # 权重 bf16 合计 123.8GiB、按块卸载只省显存不省内存，所以真正门槛
        # 是内存：int8 约 79GB、int4 约 52GB（后端 h3_quant.plan 同口径）。
        if vram >= 10 and hw.ram_total_gb >= 80 and hw.disk_free_gb >= DISK_NEED["h3"]:
            return Plan("h3", "cuda", "auto", "sequential",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存 + "
                        f"{hw.disk_free_gb:.0f}GB 磁盘：可跑 MiniMax H3 原生推理（8 步蒸馏，画质最好）")
        if vram >= 8 and hw.ram_total_gb >= 52 and hw.disk_free_gb >= DISK_NEED["h3"]:
            # 8GB 档：能跑但默认不选——139GB 下载 + int4 实验画质
            return Plan("wan-ti2v", "cuda", "auto", "sequential",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存：默认 TI2V-5B；"
                        "想试 H3 可手动选 --model h3（int4 实验档，需 52GB 内存 + 139GB 磁盘）")
        if vram >= 22 and hw.ram_total_gb >= 60:
            return Plan("wan-a14b", "cuda", "auto", "sequential",
                        f"{vram:.0f}GB 显存 + {hw.ram_total_gb:.0f}GB 内存，可上 Wan2.2-A14B 画质标杆")
        if vram >= 8 and hw.ram_total_gb >= 24:
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
    "h3": "MiniMax H3（VDN-H3 8 步蒸馏 · diffusers 原生 · 约 139GB 模型）",
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
    if plan.quantize in ("int8", "fp8", "auto"):
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


def pip_install(py: Path, args: list, desc: str):
    """args: pip install 后的参数（可含 --index-url；已指定源时不再叠加默认镜像）"""
    ui.step(desc)
    cmd = [str(py), "-m", "pip", "install", *args]
    if not any(a == "-i" or a.startswith("--index-url") for a in args):
        cmd += ["-i", PIP_INDEX]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        ui.fatal(f"安装失败: {' '.join(args)}\n请检查网络后重新运行 exe（已装部分会保留）")


# ---------------- 模型下载 ----------------
def ensure_models(rt: Path, py: Path, plan: Plan, hw: HWInfo):
    if plan.model == "h3":
        repo_args = ["--h3"]  # MiniMax H3：走 HF 缓存，约 139GB，耗时长
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
                        choices=["auto", "h3", "wan-ti2v", "wan-a14b", "anime"])
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

    hw = probe_hardware()
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
    emit_state("torch")
    pip_install(py, torch_args,
                f"安装 PyTorch（{torch_ver.upper()} 版，约 {'2.5GB' if torch_ver == 'cuda' else '200MB'}）")
    ui.ok("PyTorch 安装完成")

    emit_state("deps")
    pip_install(py, ["-r", str(rt / "backend" / "requirements.txt")],
                "安装其余依赖（FastAPI / diffusers 等，约 500MB）")
    ui.ok("依赖安装完成")

    if not args.no_download:
        emit_state("models")
        ensure_models(rt, py, plan, hw)
    else:
        ui.warn("已跳过模型下载（--no-download）")

    launch(rt, py, args.port, no_browser=args.no_browser, port_file=args.port_file)


if __name__ == "__main__":
    main()
