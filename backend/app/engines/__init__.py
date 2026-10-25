"""引擎注册表

架构口径（2026-10-22）：**ComfyUI 是唯一视频生成底座**。所有生成都经
ComfyUI（/prompt + WS 进度 + history 对账 + 产物归档）执行，NMVideo 只做
编排、资产、版本、剪辑、导出。FreeVideo 仅作为产品能力的参考对象（能力
描述、首尾帧双槽、多参考图、预览转正、队列状态），不复制其运行时，也不把
它的插件节点当作引擎身份——`freevideo_h3_*` 只是「可选工作流之一」。

所以注册表按执行通道排序：ComfyUI 通道在前且是默认引擎（`comfyui` 通用工作流 +
`h3` 专用 H3 预设）；进程内推理的 `h3_native` / `anime` / `wan22` 退为 fallback
（历史通道，ComfyUI 不可用时才用）。
"""
from .anime import AnimeEngine
from .base import (
    EXECUTION_COMFY,
    EXECUTION_NATIVE,
    BaseEngine,
    EngineMeta,
    EngineUnavailable,
    GenerationCancelled,
)
from .comfy import ComfyEngine
from .h3 import H3Engine
from .h3_comfy import H3ComfyEngine
from .wan import WanEngine

ENGINES: dict = {
    # 默认通道：提交 ComfyUI 工作流，模型与节点由 ComfyUI 侧管理
    ComfyEngine.meta.id: ComfyEngine,
    # 同一通道的专用引擎：H3 按模式套用内置 freevideo_h3_* 预设
    H3ComfyEngine.meta.id: H3ComfyEngine,
    # ---- 以下为兜底通道（进程内推理，非默认） ----
    H3Engine.meta.id: H3Engine,
    AnimeEngine.meta.id: AnimeEngine,
    WanEngine.meta.id: WanEngine,
}

# 默认引擎：ComfyUI 通道
DEFAULT_ENGINE_ID = ComfyEngine.meta.id


def get_engine(engine_id: str):
    if engine_id not in ENGINES:
        raise EngineUnavailable(f"未知引擎: {engine_id}")
    return ENGINES[engine_id]


def is_comfy_engine(engine_id: str) -> bool:
    """是否经 ComfyUI 执行（含未来新增的 ComfyUI 通道引擎）。

    未知引擎一律返回 False——重启恢复要能安全处理已下线引擎的历史任务，
    不能因为查表抛异常把整个恢复流程打断。
    """
    meta = getattr(ENGINES.get(engine_id or ""), "meta", None)
    return getattr(meta, "execution_channel", "") == EXECUTION_COMFY


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
        meta = ENGINES[item["id"]].meta
        item["supported_modes"] = list(meta.supported_modes)
        # 前端据此区分默认通道 / 兜底通道，不把兜底引擎当首选
        item["execution_channel"] = getattr(meta, "execution_channel", EXECUTION_NATIVE)
        item["is_default"] = item["id"] == DEFAULT_ENGINE_ID
    return out


def validate_engine_mode(engine_id: str, mode: str) -> None:
    engine = get_engine(engine_id)
    if mode not in engine.meta.supported_modes:
        raise ValueError(f"{engine.meta.name} 不支持「{mode}」模式；请改用支持图片条件的 ComfyUI 工作流")


__all__ = [
    "ENGINES",
    "DEFAULT_ENGINE_ID",
    "EXECUTION_COMFY",
    "EXECUTION_NATIVE",
    "BaseEngine",
    "EngineMeta",
    "EngineUnavailable",
    "GenerationCancelled",
    "get_engine",
    "is_comfy_engine",
    "list_engines",
    "validate_engine_mode",
]
