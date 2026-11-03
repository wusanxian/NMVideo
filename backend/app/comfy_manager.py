"""本机托管 ComfyUI 的生命周期管理（启动 / 停止 / 状态）

边界划分（谁管谁）：
- 「托管实例」：随本产品布局装在本机的 ComfyUI（如 FreeVideo 安装目录下的
  ComfyUI/）。应用可以替用户启动、停止——用户不用再开命令行、不用记参数。
- 「外部实例」：用户自己启动的、或远程机器上的 ComfyUI。只连接，不接管：
  我们不知道那些进程的归属，也不会替用户杀进程。

安全线：
- stop 只终止「本应用 spawn 出来的进程树」；外部起的 ComfyUI 永远不碰。
- 启停端点只在 ComfyUI 地址指向本机时生效——远程算力由服务方管理。
- 后端退出（lifespan shutdown / atexit）时顺手停掉托管进程，
  不留一个占着 8GB 显存的僵尸。

为什么启动后不等就绪再返回：ComfyUI 冷启动（加载插件、扫模型目录）可达
一分钟以上，HTTP 请求里同步等容易被前端超时。这里 spawn 完立即返回，
由后台线程盯就绪；前端轮询 GET /api/comfy/lifecycle 看 online 翻转。
"""
import atexit
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from .comfyui import ComfyUIClient
from .config import BASE_DIR

LOGS_DIR = BASE_DIR / "logs"
COMFY_LOG = LOGS_DIR / "comfyui-managed.log"

# spawn 出来的托管进程（模块级：uvicorn 单 worker，进程内全局唯一）
_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()

# 启动就绪的等待上限：插件多、模型目录大的机器冷启动偏慢，给足余量
READY_TIMEOUT_S = 120.0
READY_POLL_S = 2.0

#: 自动发现的候选目录。settings 的 comfy_dir / 环境变量优先于此列表；
#: 盘符枚举覆盖 FreeVideo 标准安装布局（X:\AI\FreeVideo\ComfyUI）。
#: 注意 ``Path("G:") / "AI"`` 生成的是盘符相对路径（G:AI\...），
#: G: 的当前目录不在根时全错——必须带根斜杠。
AUTO_CANDIDATES = tuple(
    Path(f"{drive}:/") / "AI" / "FreeVideo" / "ComfyUI"
    for drive in ("C", "D", "E", "F", "G")
)

ENV_DIR_KEY = "AIVIDEO_COMFY_DIR"


def _configured_dir() -> str | None:
    """用户显式指定的 ComfyUI 目录（settings.json，空串视为未设置）"""
    from . import settings as settings_module

    try:
        val = settings_module.get_settings().get("comfy_dir", "")
    except Exception:
        return None
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _dir_has_main(comfy_dir: Path) -> bool:
    """一个目录算不算能启动的 ComfyUI：有 main.py 即可（不猜更多结构）"""
    try:
        return (comfy_dir / "main.py").is_file()
    except OSError:
        return False


def _find_python(comfy_dir: Path) -> str | None:
    """给 ComfyUI 目录配解释器，按可靠性排序：

    1. ComfyUI 自带 venv（常见 git clone 安装布局）
    2. 上级/旁支目录的 envs/comfyui-*/（安装器布局：envs 与 ComfyUI 可能
       平级放在同一安装根下，也可能在兄弟子树里——FreeVideo 实测是
       ``安装根/FreeVideo-engine/envs/comfyui-*/``，两级都要扫）
    3. 便携版自带的 python_embeded/（Windows 免安装包布局）
    4. 系统 PATH 上的 python —— 兜底，依赖用户环境装好了依赖
    """
    envs_patterns = [comfy_dir.parent / "envs"]
    if comfy_dir.parent.is_dir():
        envs_patterns.extend(
            p / "envs" for p in comfy_dir.parent.iterdir() if p.is_dir())
    candidates = [
        comfy_dir / "venv" / "Scripts" / "python.exe",
        *(py for base in envs_patterns
          for py in sorted(base.glob("comfyui-*/Scripts/python.exe"))),
        comfy_dir / "python_embeded" / "python.exe",
    ]
    for p in candidates:
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    found = shutil.which("python.exe") or shutil.which("python")
    return found


def discover() -> dict:
    """定位本机可托管的 ComfyUI。返回 {dir, source, python, valid}，找不到 dir=None。

    source: settings（用户指定）> env（AIVIDEO_COMFY_DIR）> auto（常见路径探测）
    valid: 目录里有没有 main.py——settings 指向了坏目录时如实带出来，
    前端能显示「目录无效」，而不是装作没配置。
    """
    configured = _configured_dir()
    if configured:
        d = Path(configured)
        return {"dir": str(d), "source": "settings",
                "python": _find_python(d), "valid": _dir_has_main(d)}
    env_val = os.environ.get(ENV_DIR_KEY, "").strip()
    if env_val:
        d = Path(env_val)
        if _dir_has_main(d):
            return {"dir": str(d), "source": "env",
                    "python": _find_python(d), "valid": True}
    for d in AUTO_CANDIDATES:
        if _dir_has_main(d):
            return {"dir": str(d), "source": "auto",
                    "python": _find_python(d), "valid": True}
    return {"dir": None, "source": "none", "python": None, "valid": False}


def is_local_url() -> bool:
    """当前配置的 ComfyUI 地址是否指向本机（loopback 才提供启停）"""
    host = urllib.parse.urlsplit(ComfyUIClient().base_url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def _managed_alive() -> bool:
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def _kill_process_tree(pid: int) -> bool:
    """Windows 下杀整棵进程树：ComfyUI 会再起文本编码器等子进程，
    Popen.terminate() 只杀主进程，子进程会变成占显存的孤儿。"""
    if os.name != "nt":
        try:
            os.kill(pid, 9)
            return True
        except OSError:
            return False
    try:
        r = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def start() -> dict:
    """启动托管 ComfyUI（spawn 后立即返回，就绪与否看 lifecycle.online）"""
    global _proc
    client = ComfyUIClient()
    if client.is_available(timeout=1.5):
        return {"started": False, "reason": "already_online"}
    if not is_local_url():
        return {"started": False, "reason": "remote",
                "note": "ComfyUI 地址指向远程机器，请在服务端自行启动"}
    found = discover()
    if not found["dir"]:
        return {"started": False, "reason": "not_found",
                "note": "未找到本机 ComfyUI：可在 settings.json 配置 comfy_dir"}
    if not found.get("valid"):
        return {"started": False, "reason": "invalid_dir",
                "note": f"ComfyUI 目录无效（缺 main.py）：{found['dir']}"}
    if not found["python"]:
        return {"started": False, "reason": "no_python",
                "note": "ComfyUI 目录下没找到可用的 Python 解释器"}
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return {"started": False, "reason": "starting",
                    "note": "托管 ComfyUI 正在启动中，稍候查看状态"}

        comfy_dir = Path(found["dir"])
        port = urllib.parse.urlsplit(client.base_url).port or 8188
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_fp = open(COMFY_LOG, "ab")  # noqa: SIM115 - 交给 Popen 管生命周期
        try:
            log_fp.write(
                f"\n===== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"{found['python']} main.py --port {port} =====\n".encode("utf-8"))
            log_fp.flush()
        except OSError:
            pass
        flags = 0
        if os.name == "nt":
            # 新进程组：方便整树管理；不弹黑窗口
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        _proc = subprocess.Popen(
            [found["python"], "main.py", "--port", str(port),
             "--disable-auto-launch"],
            cwd=str(comfy_dir), stdout=log_fp, stderr=subprocess.STDOUT,
            creationflags=flags, stdin=subprocess.DEVNULL,
        )
        pid = _proc.pid

    # spawn 成功，立刻刷新可达性缓存，别让其它页面再拿 20s 前的「离线」
    from .engines.comfy import forget_available

    forget_available(client.base_url)
    threading.Thread(target=_wait_ready, args=(client.base_url,), daemon=True).start()
    return {"started": True, "pid": pid, "log_path": str(COMFY_LOG)}


def _wait_ready(base_url: str) -> None:
    """后台盯就绪；就绪/超时/早退都刷一次缓存，让轮询方尽快看到真相"""
    from .engines.comfy import forget_available

    client = ComfyUIClient(base_url)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if client.is_available(timeout=1.0):
            break
        with _proc_lock:
            if _proc is not None and _proc.poll() is not None:
                break  # 进程退了，别傻等
        time.sleep(READY_POLL_S)
    forget_available(base_url)


def stop() -> dict:
    """停止托管 ComfyUI。只杀本应用 spawn 的进程树，外部进程不碰。"""
    global _proc
    with _proc_lock:
        proc = _proc
    if proc is None or proc.poll() is not None:
        return {"stopped": False, "reason": "not_managed",
                "note": "没有本应用启动的 ComfyUI 进程（外部启动的不归我们管）"}
    pid = proc.pid
    ok = _kill_process_tree(pid)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        ok = False
    with _proc_lock:
        if _proc is proc:
            _proc = None
    from .comfyui import ComfyUIClient as _C
    from .engines.comfy import forget_available

    forget_available(_C().base_url)
    if ok:
        return {"stopped": True, "pid": pid}
    return {"stopped": False, "reason": "kill_failed", "pid": pid,
            "note": "终止命令未生效，可能需要手动结束该进程"}


def state() -> dict:
    """生命周期总览（前端启停按钮与状态行的数据源）"""
    client = ComfyUIClient()
    online = False
    try:
        online = client.is_available(timeout=1.5)
    except Exception:
        online = False
    alive = _managed_alive()
    found = discover()
    is_local = is_local_url()
    starting = alive and not online
    if online:
        note = ""
    elif starting:
        note = "托管 ComfyUI 正在启动（冷启动约需几十秒），可查看日志"
    elif not is_local:
        note = "ComfyUI 在远程机器上，由服务方管理"
    elif found["dir"] and not found.get("valid"):
        note = f"配置的 ComfyUI 目录无效（缺 main.py）：{found['dir']}"
    elif found["dir"]:
        note = "本机 ComfyUI 未运行，可一键启动"
    else:
        note = "未找到本机 ComfyUI（外部启动的请自行管理，或配置 comfy_dir）"
    log_path = str(COMFY_LOG) if (alive or COMFY_LOG.exists()) else None
    return {
        "is_local": is_local,
        "managed": found if found["dir"] else None,
        "process_alive": alive,
        "starting": starting,
        "online": online,
        "can_start": bool(is_local and found["dir"] and found.get("valid")
                          and found["python"]
                          and not alive and not online),
        "can_stop": alive,
        "note": note,
        "log_path": log_path,
    }


def shutdown_cleanup() -> None:
    """后端退出时停掉托管进程，不留占显存的僵尸。幂等，可重复调用。"""
    global _proc
    with _proc_lock:
        proc = _proc
        _proc = None
    if proc is None or proc.poll() is not None:
        return
    try:
        _kill_process_tree(proc.pid)
        proc.wait(timeout=10)
    except Exception:
        pass


atexit.register(shutdown_cleanup)
