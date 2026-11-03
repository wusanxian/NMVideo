"""ComfyUI 服务端的能力探测（H3 通道用）

H3 走 ComfyUI，就意味着「算力在对面」：显存在 ComfyUI 那台机上。本机
``hw_probe`` 描述的是「用户面前这台」，拿它去定 H3 的画布/时长上限，在远端
ComfyUI 的部署里两头都会错：

- 8GB 笔记本连 24GB 工作站 → 被按 8GB 限死在 1344×768，白白浪费算力；
- 24GB 机器连 8GB 服务器 → 放行 1920×1088，对面直接 OOM。

**只读 ComfyUI 官方接口** ``/system_stats``，不碰服务端文件系统、不认任何
自定义节点目录::

    devices[0].name       显卡名
    devices[0].vram_total 显存总字节数
    system.ram_total      内存总字节数
    system.comfyui_version

这是本项目与 ComfyUI 的边界：ComfyUI 里装什么插件、用哪套权重、什么量化格式，
都是 ComfyUI 侧的事，本程序不感知也不干预。我们要知道的只有「这台服务能画多大」。
"""
import time
from typing import Optional

from . import comfyui

#: 探测结果缓存：base_url -> (timestamp, data)。ComfyUI 地址可能改，
#: 服务端也可能换机，所以带 TTL 而不是探一次记一辈子。
_CACHE: dict = {}
_TTL = 30.0


def _clean_device_name(raw: str) -> str:
    """ComfyUI 的设备名形如 ``cuda:0 NVIDIA GeForce RTX 4060 : cudaMallocAsync``。

    去掉「cuda:N」前缀和后端标注（``: native`` / ``: cudaMallocAsync``），
    只留显卡本身——这个串会直接显示在界面上，带一堆尾巴不像在说人话。
    """
    name = raw.split(" : ")[0].strip()
    head, _, rest = name.partition(":")
    if rest and head.strip().lower().startswith("cuda"):
        # rest = "0 NVIDIA GeForce RTX 4060"：先摘掉卡序号，别把它当成型号的一部分
        parts = rest.strip().split(None, 1)
        if parts and parts[0].isdigit():
            rest = parts[1] if len(parts) > 1 else ""
        name = rest.strip()
    return name


def _parse(stats: dict) -> dict:
    """把 /system_stats 拆成我们要的几项。字段缺失一律给空值，不臆造。"""
    devices = stats.get("devices") or []
    gpu_name, vram_gib = "", -1.0
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        if dev.get("type") not in (None, "cuda"):
            continue
        try:
            total = float(dev.get("vram_total") or 0)
        except (TypeError, ValueError):
            total = 0.0
        if total > 0:
            gpu_name = _clean_device_name(str(dev.get("name") or "")) or gpu_name
            vram_gib = total / 2 ** 30
            break
    system = stats.get("system") or {}
    try:
        ram_gib = float(system.get("ram_total") or 0) / 2 ** 30
    except (TypeError, ValueError):
        ram_gib = -1.0
    return {
        "gpu_name": gpu_name,
        "vram_gib": round(vram_gib, 2),
        "ram_gib": round(ram_gib, 1),
        "comfy_version": str(system.get("comfyui_version") or ""),
    }


def probe(base_url: Optional[str] = None, force: bool = False) -> dict:
    """探测 ComfyUI 服务端的显卡与内存。

    返回::

        {
          "reachable": bool,      # 服务端答没答
          "base_url": str,
          "gpu_name": str,
          "vram_gib": float,      # -1 = 没读到
          "ram_gib": float,       # -1 = 没读到
          "comfy_version": str,
        }
    """
    url = (base_url or comfyui._default_base_url()).rstrip("/")
    now = time.time()
    if not force:
        hit = _CACHE.get(url)
        if hit and now - hit[0] < _TTL:
            return hit[1]

    data = {
        "reachable": False, "base_url": url, "gpu_name": "",
        "vram_gib": -1.0, "ram_gib": -1.0, "comfy_version": "",
    }
    try:
        # 探测走短超时：ComfyUI 没起是常态，不能让每次提交都等 5 秒
        stats = comfyui.ComfyUIClient(url).system_stats(timeout=2.5)
    except Exception:  # noqa: BLE001 - 不可达是常态（ComfyUI 没起）
        _CACHE[url] = (now, data)
        return data
    if not isinstance(stats, dict):
        _CACHE[url] = (now, data)
        return data

    data.update({"reachable": True, **_parse(stats)})
    _CACHE[url] = (now, data)
    return data


def reset_cache() -> None:
    """测试与「改完 ComfyUI 地址」时用：丢掉缓存，下次重新探。"""
    _CACHE.clear()
