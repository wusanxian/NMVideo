"""启动 / 工作空间页的数据源：一次拿齐「本地服务 → ComfyUI → 引擎 → 模型与路径」实况。

设计原则：**只报实况，不编进度**。桌面端有启动器进程能报真实阶段
（preparing / installing / ready），但浏览器端看不到那个进程，所以这里不假装
「正在安装依赖 43%」——三件事各自探测，探到什么报什么，进度就是「几件事
已经通了」。轮询成本靠缓存压住（模型目录 walk、硬件探测都不能每 2 秒跑一遍）。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter

from .. import hw_probe
from ..comfyui import ComfyUIClient
from ..config import BASE_DIR, DB_PATH, MODELS_DIR, OUTPUTS_DIR, PROJECT_DIR
from ..engines import DEFAULT_ENGINE_ID, ENGINES, is_comfy_engine

router = APIRouter(prefix="/api", tags=["launch"])

# 目录体积：走一次 os.walk 可能上万文件，60s 内复用结果
_DIR_TTL = 60.0
_dir_cache: dict = {}


def _dir_usage(path: Path, ttl: float = _DIR_TTL) -> dict:
    key = str(path)
    now = time.time()
    hit = _dir_cache.get(key)
    if hit and now - hit["at"] < ttl:
        return hit["data"]
    files = 0
    size = 0
    if path.is_dir():
        for root, _dirs, names in os.walk(path):
            for name in names:
                try:
                    size += os.path.getsize(os.path.join(root, name))
                    files += 1
                except OSError:
                    continue
    data = {"size_gb": round(size / 2**30, 2), "files": files}
    _dir_cache[key] = {"at": now, "data": data}
    return data


# 硬件探测会调 nvidia-smi（百毫秒级），30s 缓存
_HW_TTL = 30.0
_hw_cache: dict = {}


def _hardware() -> dict:
    now = time.time()
    if _hw_cache.get("at") and now - _hw_cache["at"] < _HW_TTL:
        return _hw_cache["data"]
    try:
        data = hw_probe.probe().to_dict()
    except Exception:  # noqa: BLE001
        data = {}
    _hw_cache.update({"at": now, "data": data})
    return data


def _version() -> str:
    """版本号：优先环境变量（打包时注入），否则读 desktop/package.json。"""
    env = (os.environ.get("AIVIDEO_VERSION") or "").strip()
    if env:
        return env
    pkg = PROJECT_DIR / "desktop" / "package.json"
    try:
        return str(json.loads(pkg.read_text(encoding="utf-8")).get("version") or "")
    except Exception:  # noqa: BLE001
        return ""


def _comfy_state() -> dict:
    client = ComfyUIClient()
    url = client.base_url
    online = False
    stats: dict = {}
    try:
        online = bool(client.is_available())
        if online:
            stats = client.system_stats() or {}
    except Exception:  # noqa: BLE001 - 探测失败按「未连接」报，不抛给前端
        online = False
    dev = (stats.get("devices") or [{}])[0] if stats else {}
    system = stats.get("system") or {}
    queue = {}
    if online:
        try:
            queue = client.get_queue() or {}
        except Exception:  # noqa: BLE001
            queue = {}
    return {
        "url": url,
        "online": online,
        "vram_total_gb": round(float(dev.get("vram_total") or 0) / 2**30, 1),
        "vram_free_gb": round(float(dev.get("vram_free") or 0) / 2**30, 1),
        "ram_free_gb": round(float(system.get("ram_free") or 0) / 2**30, 1),
        "queue_running": len(queue.get("queue_running") or []),
        "queue_pending": len(queue.get("queue_pending") or []),
    }


@router.get("/launch/status")
def launch_status():
    """启动页轮询端点。三个步骤各自独立探测，互不阻塞。"""
    version = _version()
    steps = []

    # 1) 本地服务：这个接口能被调到，本身就说明后端活着
    steps.append({
        "key": "backend",
        "label": "本地服务",
        "state": "done",
        "detail": f"已就绪 · v{version}" if version else "已就绪",
    })

    # 2) ComfyUI
    comfy = _comfy_state()
    if comfy["online"]:
        q = comfy["queue_running"] + comfy["queue_pending"]
        detail = f"已连接 {comfy['url']}"
        if q:
            detail += f" · 队列 {q} 个任务"
        steps.append({"key": "comfyui", "label": "ComfyUI", "state": "done", "detail": detail})
    else:
        steps.append({
            "key": "comfyui",
            "label": "ComfyUI",
            "state": "blocked",
            "detail": f"未检测到 {comfy['url']}——请先启动 ComfyUI（桌面端「系统管理」或命令行）",
        })

    # 3) 引擎：ComfyUI 通道的引擎都依赖上面的服务
    comfy_engines = [eid for eid in ENGINES if is_comfy_engine(eid)]
    native_engines = [eid for eid in ENGINES if not is_comfy_engine(eid)]
    if comfy["online"]:
        steps.append({
            "key": "engine",
            "label": "生成引擎",
            "state": "done",
            "detail": f"ComfyUI 通道 {len(comfy_engines)} 个引擎可用，"
                      f"本机兜底 {len(native_engines)} 个",
        })
    else:
        steps.append({
            "key": "engine",
            "label": "生成引擎",
            "state": "blocked",
            "detail": f"默认引擎 {DEFAULT_ENGINE_ID} 走 ComfyUI 通道，服务未连接时无法出片",
        })

    done = sum(1 for s in steps if s["state"] == "done")
    try:
        usage = shutil.disk_usage(str(MODELS_DIR if MODELS_DIR.exists() else BASE_DIR))
        disk = {"free_gb": round(usage.free / 2**30, 1), "total_gb": round(usage.total / 2**30, 1)}
    except Exception:  # noqa: BLE001
        disk = {"free_gb": -1.0, "total_gb": -1.0}

    hw = _hardware()
    return {
        "ok": comfy["online"],
        "ready": done == len(steps),
        "version": version,
        "steps": steps,
        "progress": round(done / len(steps), 3),
        "comfyui": comfy,
        "paths": {
            "project": str(PROJECT_DIR),
            "backend": str(BASE_DIR),
            "models": {"path": str(MODELS_DIR), **_dir_usage(MODELS_DIR)},
            "outputs": {"path": str(OUTPUTS_DIR), **_dir_usage(OUTPUTS_DIR)},
            "db": str(DB_PATH),
        },
        "disk": disk,
        "hardware": {
            "gpu_name": hw.get("gpu_name") or "",
            "vram_gb": hw.get("vram_gb") or 0,
            "free_vram_gb": hw.get("free_vram_gb"),
            "ram_gb": hw.get("ram_gb") or 0,
            "available_ram_gb": hw.get("available_ram_gb"),
            "device": hw.get("device") or "",
        },
    }
