"""启动 / 工作空间页的数据源：一次拿齐「本地服务 → ComfyUI → 引擎 → 模型与路径」实况。

设计原则：**只报实况，不编进度**。桌面端有启动器进程能报真实阶段
（preparing / installing / ready），但浏览器端看不到那个进程，所以这里不假装
「正在安装依赖 43%」——三件事各自探测，探到什么报什么，进度就是「几件事
已经通了」。轮询成本靠缓存压住（模型目录 walk、硬件探测都不能每 2 秒跑一遍）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import hw_probe
from .. import resource_guard
from .. import resource_watch
from ..comfyui import ComfyUIClient
from ..config import BASE_DIR, DB_PATH, MODELS_DIR, OUTPUTS_DIR, PROJECT_DIR
from ..engines import DEFAULT_ENGINE_ID, ENGINES, is_comfy_engine
from ..engines import h3_quant
from ..engines.comfy import forget_available, probe_available

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


def warm_hardware() -> None:
    """预热硬件探测缓存（main.py 启动时后台调用，别让首屏等 torch import）。"""
    _hardware()


def warm_comfy() -> None:
    """预热 ComfyUI 可达性缓存：离线时这笔探测要等满 1.5s 连接超时。"""
    probe_available(ComfyUIClient().base_url)


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


# =====================================================================
# 环境准备清单（首启引导）：依赖探测 + 引擎推荐
# =====================================================================
# H3 权重 123.8GiB（14+14 分片）→ 磁盘实占 139GB（见 engines/h3_quant.py 文档实测）
H3_DISK_GB = 139.0

# 运行依赖：只用 find_spec 查「在不在」，不 import——
# import torch 要 2 秒起，启动页每 2 秒轮一次不能这么花。
_DEP_ITEMS = (
    ("torch", "PyTorch", "视频生成推理底座"),
    ("diffusers", "diffusers", "H3 / Wan 管线"),
    ("torchao", "torchao", "权重量化 int4/int8/fp8"),
    ("edge_tts", "edge-tts", "神经网络配音（联网）"),
    ("pyttsx3", "pyttsx3", "离线配音兜底（SAPI5）"),
    ("imageio_ffmpeg", "imageio-ffmpeg", "内置 ffmpeg，合成 MP4 / 导出成片"),
)


def _dep_state() -> list:
    out = []
    for mod, name, why in _DEP_ITEMS:
        try:
            installed = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):  # 半装状态 / 父包损坏
            installed = False
        out.append({"id": mod, "name": name, "why": why, "installed": installed})
    return out


def _recommend_engine(hw: dict) -> dict:
    """按「显存 × 内存 × 磁盘」给出引擎建议。

    口径跟 engines/h3_quant.py 一致：H3 的门槛是**内存**（权重要常驻），
    显存只决定常驻还是按块流式。磁盘不够也装不下，所以单独算一条。
    推荐结论不写死，全部由本机实测值推出来——写死 h3 会在 32GB 机器上
    给出必然 OOM 的建议。
    """
    vram = float(hw.get("vram_gb") or 0)
    ram = float(hw.get("ram_gb") or 0)
    cap = str(hw.get("compute_cap") or "")
    disk_free = float(hw.get("disk_free_gb") or 0)
    plan = h3_quant.plan(vram, ram, cap)

    h3_blockers = []
    if not plan.available:
        h3_blockers.append(plan.reason)
    if 0 < disk_free < H3_DISK_GB:
        h3_blockers.append(
            f"磁盘可用 {disk_free:.0f}GB，小于 H3 权重所需 {H3_DISK_GB:.0f}GB。"
        )
    h3_ok = plan.available and not (0 < disk_free < H3_DISK_GB)

    # probe() 只在「torch 认到卡」时才把 device 设成 cuda；
    # to_dict() 历史上没带 torch_cuda_available 字段，两个都认一下。
    cuda_ok = bool(hw.get("torch_cuda_available")) or str(hw.get("device") or "") == "cuda"
    model_id = str(hw.get("model_id") or "ti2v-5b")
    quant = str(hw.get("quant") or "none")
    if cuda_ok:
        wan_note = f"{model_id} · {quant} 量化 · 8GB 显存可跑 480p"
    else:
        wan_note = "未检测到可用 CUDA：会退回 CPU 推理（小时级），先修驱动 / torch"

    # H3QuantPlan 是 dataclass，档位中文名在 TIER_LABEL 里（to_dict() 才拼进去）
    tier_label = h3_quant.TIER_LABEL.get(plan.tier, plan.tier or "不可用")
    h3_option = {
        "id": "h3",
        "name": "MiniMax H3",
        "subtitle": "画质旗舰 · 走 ComfyUI 通道",
        "available": h3_ok,
        "tier": plan.tier,
        "tier_label": tier_label if h3_ok else f"{tier_label}（不可用）",
        "weights_gb": round(plan.weights_gib, 1),
        "need_ram_gb": plan.min_ram_gib,
        "need_disk_gb": H3_DISK_GB,
        "max_pixels": plan.max_pixels,
        "note": plan.note if plan.available else plan.reason,
        "blockers": h3_blockers,
    }
    wan_option = {
        "id": "wan22",
        "name": "Wan 2.2 TI2V-5B",
        "subtitle": "低显存主力 · 480p / 5 秒",
        "available": cuda_ok,
        "tier": quant,
        "tier_label": quant,
        "weights_gb": 0.0,
        "need_ram_gb": 0,
        "need_disk_gb": 0,
        "max_pixels": 0,
        "note": wan_note,
        "blockers": [] if cuda_ok else [wan_note],
    }

    recommended = "h3" if h3_ok else "wan22"
    if h3_ok:
        summary = f"优先 MiniMax H3（{tier_label}，权重 {plan.weights_gib:.0f}GiB）"
    elif cuda_ok:
        summary = (
            f"H3 需 {plan.min_ram_gib}GB 内存 / {H3_DISK_GB:.0f}GB 磁盘，"
            f"本机 {ram:.0f}GB 内存 / {disk_free:.0f}GB 磁盘不满足 → 推荐 Wan 2.2 TI2V-5B"
        )
    else:
        summary = "未检测到可用 CUDA，先把显卡 / torch 修好再看引擎"

    return {
        "recommended": recommended,
        "summary": summary,
        "options": [h3_option, wan_option],
    }


def _version() -> str:
    """版本号，按「打包后 / 开发态」两个世界各找一处：

    1. `AIVIDEO_VERSION` 环境变量（外部注入）
    2. 运行根目录的 `VERSION` 文件——PyInstaller 包里没有 desktop/ 目录，
       只有 packaging/build.py 写的 `payload/VERSION`（内容 "0.1.9 2026-10-30"）
    3. `desktop/package.json`（源码运行时才有）
    """
    env = (os.environ.get("AIVIDEO_VERSION") or "").strip()
    if env:
        return env
    stamp = PROJECT_DIR / "VERSION"
    try:
        first = stamp.read_text(encoding="utf-8").split()[0].strip()
        if first:
            return first
    except Exception:  # noqa: BLE001 - 没有这个文件就走下一条
        pass
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
    # 走引擎层的共享缓存：启动页每 2s 轮一次，直连探测会让每次请求都等满超时
    # （ComfyUI 没起时首次实测 8s）。缓存 10 秒，用户刚拉起 ComfyUI 也就差这 10 秒。
    try:
        online = probe_available(url)
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
def launch_status(refresh: bool = False):
    """启动页轮询端点。三个步骤各自独立探测，互不阻塞。

    `refresh=1` 跳过探测缓存——「重新检测」按钮用：用户刚把 ComfyUI 拉起来，
    不该让他等缓存过期。
    """
    if refresh:
        forget_available()
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
        # 设置页「兼容性」要显示的是实际生效的策略，不是文档里的默认值
        "resource": {
            "guard": resource_guard.guard_mode(),
            "watchdog": resource_watch.watchdog_enabled(),
            "ram_floor_gb": resource_watch.DEFAULT_FLOOR_GB,
            "ram_tight_gb": resource_watch.DEFAULT_FLOOR_GB + resource_watch.TIGHT_MARGIN_GB,
        },
        "hardware": {
            "gpu_name": hw.get("gpu_name") or "",
            "vram_gb": hw.get("vram_gb") or 0,
            "free_vram_gb": hw.get("free_vram_gb"),
            "ram_gb": hw.get("ram_gb") or 0,
            "available_ram_gb": hw.get("available_ram_gb"),
            "device": hw.get("device") or "",
            # 首启引导要按「显存 × 内存 × 算力」推引擎档位，算力不能缺
            "compute_cap": hw.get("compute_cap") or "",
            "disk_free_gb": hw.get("disk_free_gb") or 0,
            "torch_cuda_available": bool(hw.get("torch_cuda_available"))
            or str(hw.get("device") or "") == "cuda",
            "wan_model_id": hw.get("model_id") or "",
            "wan_quant": hw.get("quant") or "",
        },
        # 首启引导：引擎推荐（本机实测推出来的）+ 运行依赖在不在
        "recommend": _recommend_engine(hw),
        "deps": _dep_state(),
    }


# =====================================================================
# 打开目录（首启引导 / 设置页的「打开目录」按钮）
# =====================================================================
# 只认白名单键，绝不接受前端传任意路径——这个接口能拉起系统文件管理器，
# 拿路径当参数等于把「打开任意目录」暴露给任何能访问本机 127.0.0.1 的页面。
_FOLDER_KEYS = {
    "outputs": OUTPUTS_DIR,
    "models": MODELS_DIR,
    "backend": BASE_DIR,
    "project": PROJECT_DIR,
}


class OpenFolderBody(BaseModel):
    key: str = "outputs"


def _reveal(path: Path) -> None:
    """在系统文件管理器里打开目录（跨平台）。"""
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - 受管白名单目录，非用户输入
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607


@router.post("/launch/open-folder")
def open_folder(body: OpenFolderBody):
    """打开受管目录（生成视频 / 模型 / 运行目录）。

    服务端在本机跑，所以这里直接拉起资源管理器——比走 Electron IPC 更通用，
    浏览器里打开页面时同样可用。
    """
    target = _FOLDER_KEYS.get((body.key or "").strip())
    if target is None:
        raise HTTPException(status_code=400, detail=f"不支持的目录键：{body.key}")
    path = Path(target)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"目录不可用：{path}（{exc}）") from exc
    if not path.is_dir():
        raise HTTPException(status_code=500, detail=f"不是目录：{path}")
    try:
        _reveal(path)
    except Exception as exc:  # noqa: BLE001 - 无桌面环境 / 权限不足
        raise HTTPException(status_code=500, detail=f"无法打开目录：{exc}") from exc
    return {"ok": True, "path": str(path)}
