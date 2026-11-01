"""本地模型库：哪些权重可以下、下没下完、正在下的那一份到哪儿了。

一条硬规则：**不替用户决定下载**。
Wan 2.2 的权重是 32GB（TI2V-5B）/ 119GB（A14B）这种量级，值不值得占这块磁盘
是用户自己的事。所以：
  - 启动器首启不再自动拉 Wan，只在用户显式 ``--model wan-*`` 时才下；
  - 这个模块不做任何「缺什么就自动补」的逻辑，只有前端点了「下载」才动手。

状态只报实况：目录里有多少字节、关键索引文件在不在。进度条的分子是真实
磁盘增量，分母是仓库体积估计值（界面注明「约」），不编造数字。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import BASE_DIR, HF_CACHE_DIR, HF_ENDPOINT, MODELS_DIR

#: 下载脚本（走 hf-mirror + 断点续传 + 文件清单校验，见脚本头注释）
SCRIPT = BASE_DIR / "scripts" / "download_models.py"

#: 状态缓存：目录体积统计要递归，30 秒内复用（下载中不走缓存）
_SCAN_TTL = 30.0
_scan_cache: Dict[str, Tuple[float, dict]] = {}
_scan_lock = threading.Lock()


@dataclass(frozen=True)
class ModelItem:
    id: str
    label: str              # 功能名，界面主标题（不先亮模型名）
    detail: str             # 这个权重干什么用
    engine: str             # 归哪个引擎用（wan22 / anime）
    repo: str               # HF 仓库
    subdir: str             # MODELS_DIR 下的目录名
    marker: str             # 判定「下全了」的关键文件（相对 subdir）
    approx_gb: float        # 体积估计，进度分母用（界面标注「约」）
    script_args: Tuple[str, ...]  # 传给 download_models.py 的参数
    min_gb: float = 0.0     # 「真的能用」的最小体积；只靠 marker 会误判（见 scan）


MODEL_ITEMS: Tuple[ModelItem, ...] = (
    ModelItem(
        "wan-ti2v-5b",
        "低显存 · 快速出片权重",
        "8GB 显存直接跑 480p / 5 秒档；本程序不再默认下载，需要时自己点",
        "wan22",
        "Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        "wan-ti2v-5b",
        "model_index.json",
        32.0,
        ("--wan-ti2v-5b",),
        20.0,
    ),
    ModelItem(
        "wan-t2v-a14b",
        "高画质出片权重（超大）",
        "双专家 MoE，画质标杆；需 60GB+ 内存 / 20GB+ 显存 / 119GB 磁盘",
        "wan22",
        "Wan-AI/Wan2.2-T2V-A14B-Diffusers",
        "wan-t2v-a14b",
        "model_index.json",
        119.0,
        ("--wan-a14b",),
        80.0,
    ),
    ModelItem(
        "anime",
        "动漫引擎权重",
        "动漫风格底模 + 运动模块，CPU 也能跑（分钟级出片），约 5GB",
        "anime",
        "dreamlike-art/dreamlike-anime-1.0",
        "anime-diffusers",
        "model_index.json",
        5.0,
        (),
        3.5,
    ),
)

_BY_ID = {item.id: item for item in MODEL_ITEMS}


def get_item(model_id: str) -> Optional[ModelItem]:
    return _BY_ID.get(model_id)


# ---------------------------------------------------------------- 体积统计
def _dir_bytes(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def scan(item: ModelItem, fresh: bool = False) -> dict:
    """单个模型的本机状态：ready / partial / absent + 实际体积。

    **只看 marker 会误判**：HF 仓库根目录的 ``model_index.json`` 只有几百字节，
    刚开下就落地了——真机上于是出现「界面显示已就绪，引擎一加载就报缺权重」。
    所以 ready 还要过一道体积下限（``min_gb``），否则一律算 partial。
    """
    key = item.id
    now = time.time()
    if not fresh:
        with _scan_lock:
            hit = _scan_cache.get(key)
        if hit and now - hit[0] < _SCAN_TTL:
            return hit[1]

    path = MODELS_DIR / item.subdir
    size = _dir_bytes(path)
    size_gb = size / 2**30
    marker = path / item.marker
    if marker.exists() and size_gb >= item.min_gb:
        state = "ready"
    elif size > 0:
        state = "partial"
    else:
        state = "absent"
    data = {
        "state": state,
        "size_gb": round(size_gb, 2),
        "path": str(path),
    }
    with _scan_lock:
        _scan_cache[key] = (now, data)
    return data


def _forget_scan(model_id: str) -> None:
    with _scan_lock:
        _scan_cache.pop(model_id, None)


# ---------------------------------------------------------------- 下载任务
@dataclass
class Task:
    model_id: str
    state: str = "running"          # running | done | error | cancelled
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0
    returncode: Optional[int] = None
    message: str = ""
    size_start: int = 0
    log_path: Optional[Path] = None
    # 只在内存里留最近几行给接口返回，完整日志在 log_path 上（用户可自己看）
    log: List[str] = field(default_factory=list)

    def to_dict(self, item: ModelItem) -> dict:
        cur = _dir_bytes(MODELS_DIR / item.subdir)
        gained = max(0, cur - self.size_start)
        target = item.approx_gb * 2**30
        pct = 0.0
        if target > 0:
            pct = min(99.0, gained / target * 100)
        if self.state == "done":
            pct = 100.0
        return {
            "model_id": self.model_id,
            "state": self.state,
            "percent": round(pct, 1),
            "gained_gb": round(gained / 2**30, 2),
            "elapsed_s": round((self.ended_at or time.time()) - self.started_at, 1),
            "message": self.message,
            "tail": _tail(self.log_path) or self.log[-6:],
        }


def _tail(path: Optional[Path], n: int = 6) -> List[str]:
    """读下载日志尾部。

    进度条用 ``\\r`` 原地刷新，按行迭代（readline）会一行都读不到——
    真机上就表现为「任务在跑但界面什么都不显示」。所以直接读文件尾并按
    ``\\r`` / ``\\n`` 一起切。
    """
    if path is None:
        return []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            raw = fh.read()
    except OSError:
        return []
    parts = [p.strip() for p in raw.decode("utf-8", "replace")
             .replace("\r", "\n").split("\n")]
    return [p for p in parts if p][-n:]


_tasks: Dict[str, Task] = {}
_procs: Dict[str, subprocess.Popen] = {}
_task_lock = threading.Lock()


def _run(task: Task, item: ModelItem) -> None:
    # PYTHONUNBUFFERED + -u：子进程输出重定向到文件时是块缓冲，8KB 以下一个字节
    # 都看不到（真机表现：任务在跑、进度条不动、日志空白）。必须强制不缓冲。
    env = dict(os.environ, HF_ENDPOINT=HF_ENDPOINT, HF_HOME=str(HF_CACHE_DIR),
               PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-u", str(SCRIPT), *item.script_args]
    log_path = MODELS_DIR / f".download-{item.id}.log"
    task.log_path = log_path
    # 输出直接落文件：管道里读 tqdm 的 \r 刷新既读不到整行也会被缓冲卡住，
    # 落盘后再按需 tail，界面反而更实时
    try:
        fh = open(log_path, "wb")  # noqa: SIM115 - 句柄要给子进程用到退出
    except OSError:
        fh = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            stdout=fh if fh is not None else subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:  # noqa: BLE001 - 解释器/脚本缺失
        if fh is not None:
            fh.close()
        task.state = "error"
        task.message = f"无法启动下载：{exc}"
        task.ended_at = time.time()
        return

    with _task_lock:
        _procs[item.id] = proc
    try:
        proc.wait()
        task.returncode = proc.returncode
        _forget_scan(item.id)
        ok = proc.returncode == 0
        # 退出码为 0 也要复查 marker：Xet 静默丢文件的老坑，脚本虽已校验，
        # 这里再确认一次「真的能用」，免得界面打勾但引擎加载报错
        state = scan(item, fresh=True)
        if ok and state["state"] == "ready":
            task.state = "done"
            task.message = f"已就绪（{state['size_gb']:.1f} GB）"
        elif ok:
            task.state = "error"
            task.message = "下载进程结束，但权重不完整，请重试（支持断点续传）"
        elif task.state == "cancelled":
            task.message = "已取消，已下部分会保留"
        else:
            task.state = "error"
            task.message = "下载失败，可重试（支持断点续传）"
    finally:
        if fh is not None:
            fh.close()
        task.ended_at = time.time()
        with _task_lock:
            _procs.pop(item.id, None)


def start(model_id: str) -> Tuple[bool, str]:
    """启动下载。同一时刻只允许一个任务（带宽和磁盘都受不了两份并行）。"""
    item = get_item(model_id)
    if item is None:
        return False, f"未知模型：{model_id}"
    with _task_lock:
        for tid, t in _tasks.items():
            if t.state == "running":
                if tid == model_id:
                    return False, "这个权重正在下载"
                other = _BY_ID.get(tid)
                return False, f"已有下载任务在进行（{other.label if other else tid}），等它跑完再下"
        task = Task(model_id=model_id, size_start=_dir_bytes(MODELS_DIR / item.subdir))
        _tasks[model_id] = task
    threading.Thread(target=_run, args=(task, item), daemon=True,
                     name=f"model-dl-{model_id}").start()
    return True, "已开始下载"


def cancel(model_id: str) -> Tuple[bool, str]:
    with _task_lock:
        proc = _procs.get(model_id)
        task = _tasks.get(model_id)
    if proc is None or task is None or task.state != "running":
        return False, "没有正在进行的下载任务"
    task.state = "cancelled"
    try:
        proc.terminate()
    except Exception as exc:  # noqa: BLE001
        return False, f"取消失败：{exc}"
    return True, "正在停止…"


def snapshot() -> dict:
    """模型库全局状态：清单 + 下载任务 + 磁盘余量。"""
    items = []
    for item in MODEL_ITEMS:
        with _task_lock:
            t = _tasks.get(item.id)
        # 下载中必须实时统计：走 30s 缓存的话目录在长，界面会一直显示「未下载」
        state = scan(item, fresh=bool(t and t.state == "running"))
        task = t.to_dict(item) if t is not None else None
        items.append({
            "id": item.id,
            "label": item.label,
            "detail": item.detail,
            "engine": item.engine,
            "repo": item.repo,
            "approx_gb": item.approx_gb,
            "state": state["state"],
            "size_gb": state["size_gb"],
            "path": state["path"],
            "task": task,
        })
    running = any(i["task"] and i["task"]["state"] == "running" for i in items)
    try:
        import shutil
        usage = shutil.disk_usage(str(MODELS_DIR))
        disk_free_gb = round(usage.free / 2**30, 1)
    except Exception:  # noqa: BLE001
        disk_free_gb = -1.0
    return {
        "models_dir": str(MODELS_DIR),
        "disk_free_gb": disk_free_gb,
        "downloading": running,
        "items": items,
    }


def wan_ready(model_id: str = "ti2v-5b") -> bool:
    """Wan 权重是否真的就位（引擎卡与推荐文案用）。

    和界面同一套判定：marker 在、体积也够才算就绪。
    """
    key = (model_id or "ti2v-5b").strip()
    item = get_item("wan-t2v-a14b" if key in ("t2v-a14b", "wan-t2v-a14b") else "wan-ti2v-5b")
    if item is None:
        return False
    return scan(item)["state"] == "ready"
