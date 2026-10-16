"""引擎注册表"""
from .anime import AnimeEngine
from .base import BaseEngine, EngineMeta, EngineUnavailable, GenerationCancelled
from .comfy import ComfyEngine
from .wan import WanEngine

ENGINES: dict = {
    AnimeEngine.meta.id: AnimeEngine,
    WanEngine.meta.id: WanEngine,
    ComfyEngine.meta.id: ComfyEngine,
}


def get_engine(engine_id: str):
    if engine_id not in ENGINES:
        raise EngineUnavailable(f"未知引擎: {engine_id}")
    return ENGINES[engine_id]


def list_engines() -> list:
    """引擎列表。支持 describe() 动态状态（如 Wan 引擎按硬件/模型探测）。"""
    out = []
    for c in ENGINES.values():
        if hasattr(c, "describe"):
            out.append(c().describe())
        else:
            out.append(
                {
                    "id": c.meta.id,
                    "name": c.meta.name,
                    "description": c.meta.description,
                    "status": c.meta.status,
                    "note": c.meta.note,
                }
            )
    return out


__all__ = [
    "ENGINES",
    "BaseEngine",
    "EngineMeta",
    "EngineUnavailable",
    "GenerationCancelled",
    "get_engine",
    "list_engines",
]
