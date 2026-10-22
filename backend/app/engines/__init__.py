"""引擎注册表"""
from .anime import AnimeEngine
from .base import BaseEngine, EngineMeta, EngineUnavailable, GenerationCancelled
from .comfy import ComfyEngine
from .h3 import H3Engine
from .wan import WanEngine

ENGINES: dict = {
    # h3 置于首位：默认引擎（MiniMax H3 / VDN-H3 8 步蒸馏），由 diffusers
    # ModularPipeline 原生推理——不需要 ComfyUI，也不需要 FreeVideo 插件
    H3Engine.meta.id: H3Engine,
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
    for item in out:
        item["supported_modes"] = list(ENGINES[item["id"]].meta.supported_modes)
    return out


def validate_engine_mode(engine_id: str, mode: str) -> None:
    engine = get_engine(engine_id)
    if mode not in engine.meta.supported_modes:
        raise ValueError(f"{engine.meta.name} 不支持「{mode}」模式；请改用支持图片条件的 ComfyUI 工作流")


__all__ = [
    "ENGINES",
    "BaseEngine",
    "EngineMeta",
    "EngineUnavailable",
    "GenerationCancelled",
    "get_engine",
    "list_engines",
]
