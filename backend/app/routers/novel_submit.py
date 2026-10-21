"""PR-N05：提交批次 REST API（预览/正式生成）。

职责边界：批次生命周期（建/查/重试/取消）；任务创建复用工作台
POST /shots/{id}/generate 端点，审核采用复用现有 adopt 端点——本路由
不重复实现任何生成与审核逻辑。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import novel as novel_mod
from .. import novel_submit as submit_mod

sp_router = APIRouter(prefix="/api/novels/{novel_id}", tags=["novel-submit"])


def _guard_novel(novel_id: str) -> None:
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except submit_mod.SubmitError as e:
        raise HTTPException(e.status, str(e)) from e


class SubmitCreate(BaseModel):
    kind: str = Field("preview", pattern="^(preview|final)$")
    quality: str = Field("draft", pattern="^(draft|standard|high)$")
    engine: str = "anime"
    # 可选：只提交部分镜头（默认全部已审核镜头）
    shot_item_ids: Optional[list] = None
    # 预算闸：预计总耗时超过上限 → 400 阻断（秒）
    budget_limit_sec: int = Field(3600, ge=60, le=30 * 24 * 3600)
    note: str = Field("", max_length=300)


@sp_router.post("/episodes/{episode_id}/shots/submit", status_code=201)
def create_submission(novel_id: str, episode_id: str, data: SubmitCreate):
    """建提交批次（preview=低质量预览 / final=正式生成），返回批次 + 估算。

    前置校验：分镜已审核且已交接项目、无并发批次/任务、逐镜头模式与
    参考图校验、预算闸。final 批次要求每个镜头已有采用版本（人审门禁）。
    """
    _guard_novel(novel_id)
    return _run(submit_mod.create_submission, novel_id, episode_id,
                kind=data.kind, quality=data.quality, engine=data.engine,
                shot_item_ids=data.shot_item_ids,
                budget_limit_sec=data.budget_limit_sec, note=data.note)


@sp_router.get("/episodes/{episode_id}/shots/submissions")
def list_submissions(novel_id: str, episode_id: str):
    """批次列表 + 逐条目状态/进度。查询时顺带推进活跃批次（拉驱动）。"""
    _guard_novel(novel_id)
    for sid in submit_mod.active_submission_ids(
            novel_id=novel_id, episode_id=episode_id):
        submit_mod.advance_submission(sid)
    return _run(submit_mod.list_submissions, novel_id, episode_id)


@sp_router.post("/episodes/{episode_id}/shots/submissions/{sid}/retry")
def retry_submission(novel_id: str, episode_id: str, sid: str):
    """重试失败条目（只重试失败镜，成功镜头不动）。"""
    _guard_novel(novel_id)
    return _run(submit_mod.retry_submission, novel_id, episode_id, sid)


@sp_router.post("/episodes/{episode_id}/shots/submissions/{sid}/cancel")
def cancel_submission(novel_id: str, episode_id: str, sid: str):
    """取消批次：未入队条目作废、排队任务撤下；进行中的生成不杀，
    等其自然完成后批次转「已取消」。"""
    _guard_novel(novel_id)
    return _run(submit_mod.cancel_submission, novel_id, episode_id, sid)
