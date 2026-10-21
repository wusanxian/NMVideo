"""PR-N04：结构化分镜 REST API。

职责边界：只做「审核态剧本 → 结构化分镜草案 → 审核 → 交接项目分镜」的
前期制作数据；不自动启动视频生成、不覆盖已有制作成果。所有 AI 草案均可
编辑、可审核、可保存版本；镜头可挂章节快照定位原小说。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import novel as novel_mod
from .. import novel_shots as shots_mod

router = APIRouter(prefix="/api", tags=["novel-shots"])
sp_router = APIRouter(prefix="/api/novels/{novel_id}", tags=["novel-shots"])


def _guard_novel(novel_id: str) -> None:
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")


def _err(e: shots_mod.ShotPlanError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except shots_mod.ShotPlanError as e:
        raise _err(e) from e


# =====================================================================
# 结构化分镜（生成 / 查询 / 编辑 / 排序 / 审核 / 交接）
# =====================================================================
@sp_router.post("/episodes/{episode_id}/shots/generate")
def generate_plan(novel_id: str, episode_id: str):
    """LLM 生成结构化分镜草案（替换该分集既有计划；旧计划镜头随级联删除）。

    依赖：审核通过的动漫剧本 + 故事圣经；LLM 未配置 → 400 引导去设置页。
    """
    _guard_novel(novel_id)
    return _run(shots_mod.generate_plan, novel_id, episode_id)


@sp_router.get("/episodes/{episode_id}/shots")
def get_plan(novel_id: str, episode_id: str):
    """分镜计划详情（含全部镜头，按 seq 排序；未生成过 → 404）。"""
    _guard_novel(novel_id)
    data = _run(shots_mod.get_plan, novel_id, episode_id)
    if not data:
        raise HTTPException(404, "分镜计划尚未生成：请先「生成结构化分镜」")
    return data


class ItemUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=40)
    scene_title: Optional[str] = Field(None, max_length=120)
    action: Optional[str] = Field(None, max_length=300)
    dialogue: Optional[str] = Field(None, max_length=300)
    shot_size: Optional[str] = Field(None, max_length=10)
    camera_movement: Optional[str] = Field(None, max_length=10)
    duration_sec: Optional[float] = Field(None, ge=1, le=15)
    mode: Optional[str] = Field(None, pattern="^(t2v|i2v|first_last|continuation)$")
    character_names: Optional[list] = None
    position: Optional[str] = Field(None, max_length=120)
    emotion: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=500)


@sp_router.put("/episodes/{episode_id}/shots/{item_id}")
def update_item(novel_id: str, episode_id: str, item_id: str, data: ItemUpdate):
    """编辑镜头（字段白名单在数据层收敛；审核后改动自动回草稿态）。"""
    _guard_novel(novel_id)
    return _run(shots_mod.update_item, novel_id, episode_id, item_id,
                {k: v for k, v in data.model_dump(exclude_unset=True).items()})


class ReorderBody(BaseModel):
    direction: str = Field(..., pattern="^(up|down)$")


@sp_router.post("/episodes/{episode_id}/shots/{item_id}/reorder")
def reorder_item(novel_id: str, episode_id: str, item_id: str, data: ReorderBody):
    """镜头上移/下移一位（与相邻镜头交换顺序；顺序即故事顺序）。"""
    _guard_novel(novel_id)
    return _run(shots_mod.reorder_item, novel_id, episode_id, item_id,
                data.direction)


class ApproveBody(BaseModel):
    note: str = ""


@sp_router.post("/episodes/{episode_id}/shots/approve")
def approve_plan(novel_id: str, episode_id: str, data: ApproveBody):
    """审核通过：整计划 + 全部镜头存为不可变版本快照，状态 → 已审核。"""
    _guard_novel(novel_id)
    return _run(shots_mod.approve_plan, novel_id, episode_id, note=data.note)


@sp_router.get("/episodes/{episode_id}/shots/versions/{version}")
def get_plan_version(novel_id: str, episode_id: str, version: int):
    """分镜计划历史版本快照（不可变，可回看当时审核态）。"""
    _guard_novel(novel_id)
    return _run(shots_mod.get_plan_version, novel_id, episode_id, version)


@sp_router.get("/episodes/{episode_id}/shots/validate")
def validate_plan(novel_id: str, episode_id: str):
    """分镜计划 ↔ 故事圣经一致性校验（只读，不阻断任何操作）。

    返回未知人物名（含别名解析）、未匹配场景、圣经版本过期（stale）
    与草稿态提示；warnings 汇总为面向用户的中文提示列表。
    """
    _guard_novel(novel_id)
    return _run(shots_mod.validate_plan, novel_id, episode_id)


@sp_router.post("/episodes/{episode_id}/shots/handoff")
def handoff_plan(novel_id: str, episode_id: str):
    """交接：审核通过的分镜计划 → 写入目标项目的 Scene/Shot（幂等）。

    只建数据（场景/分镜/角色卡），**不启动视频生成**；目标项目已有制作
    成果 → 400 拒绝，绝不静默覆盖。
    """
    _guard_novel(novel_id)
    return _run(shots_mod.handoff_plan, novel_id, episode_id)
