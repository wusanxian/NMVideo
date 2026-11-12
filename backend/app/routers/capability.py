"""能力分配 API：这台机器能跑什么模型、什么画布、什么画质档。

前端不该自己拼判断——「显存够不够」「节点在不在」「磁盘够不够」三件事
各自有真值来源，散在页面里必然出现两处结论不一致。统一从这里取。
"""
import shutil
import time

from fastapi import APIRouter

from .. import capability, comfyui, model_store

router = APIRouter(prefix="/api/capability", tags=["capability"])

#: ComfyUI 节点表缓存。929 个节点的 JSON 有几 MB，页面每切一次都拉一遍太浪费；
#: 但插件装/卸要能反映出来，所以带 TTL 而不是记一辈子。
_NODES_CACHE: dict = {}
_NODES_TTL = 60.0


def _comfy_url() -> str:
    return comfyui._default_base_url()


def _nodes(url: str) -> list | None:
    """ComfyUI 的节点名列表；连不上返回 ``None``（不是空列表）。

    这个区别很重要：``None`` 让 capability 说「没连上，无法确认」，
    而不是把「ComfyUI 没启动」错报成「插件没装」——后者会让用户去重装插件。
    """
    now = time.time()
    hit = _NODES_CACHE.get(url)
    if hit and now - hit[0] < _NODES_TTL:
        return hit[1]
    try:
        client = comfyui.ComfyUIClient(url)
        if not client.is_available():
            return None
        info = client.object_info()
    except Exception:  # noqa: BLE001 - 探测失败按「没连上」处理
        return None
    names = list(info.keys())
    _NODES_CACHE[url] = (now, names)
    return names


def _disk_free() -> float:
    """模型目录所在盘的剩余空间（GiB）。权重装在这块盘上，看别的盘没意义。"""
    try:
        return round(shutil.disk_usage(str(model_store.MODELS_DIR)).free / 2**30, 1)
    except OSError:
        return 0.0


@router.get("")
def get_capability(comfy_url: str = ""):
    url = (comfy_url or "").strip() or _comfy_url()
    return capability.assess(
        disk_free_gib=_disk_free(),
        nodes=_nodes(url),
        comfy_url=url,
    )
