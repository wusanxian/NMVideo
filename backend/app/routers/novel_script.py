"""PR-N03：自动分集与动漫剧本 REST API。

职责边界：只做「圣经 → 分集 → 动漫剧本 → 交接项目」的前期制作数据；
不自动启动视频生成、不触碰已有项目/镜头/成片。所有 AI 草案均可编辑、
可审核、可保存版本；分集 chapter_refs 与剧本快照都能定位原小说。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import novel as novel_mod
from .. import novel_script as script_mod

router = APIRouter(prefix="/api", tags=["novel-script"])
ep_router = APIRouter(prefix="/api/novels/{novel_id}", tags=["novel-script"])


def _guard_novel(novel_id: str) -> None:
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")


def _err(e: script_mod.ScriptError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except script_mod.ScriptError as e:
        raise _err(e) from e


# =====================================================================
# 分集大纲（AI 规划）
# =====================================================================
class OutlineGenerate(BaseModel):
    episode_count: Optional[int] = Field(None, description="期望集数（提示，非硬性）")
    target_duration: Optional[int] = Field(None, description="单集目标时长（秒）")


@ep_router.post("/episodes/generate")
def generate_outline(novel_id: str, data: OutlineGenerate):
    """LLM 生成分集大纲草案（替换既有分集；旧分集的剧本随级联删除）。

    依赖故事圣经 + 已完成章节分析；LLM 未配置 → 400 引导去设置页。
    """
    _guard_novel(novel_id)
    return _run(script_mod.generate_outline, novel_id,
                episode_count=data.episode_count, target_duration=data.target_duration)


@ep_router.get("/episodes")
def list_episodes(novel_id: str):
    """分集列表（带剧本摘要：状态/版本/交接去向）。"""
    _guard_novel(novel_id)
    return script_mod.list_episodes(novel_id)


class EpisodeCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    logline: str = ""
    target_duration_sec: int = Field(90, ge=30, le=300)
    chapter_ids: list = Field(default_factory=list)
    beats: list = Field(default_factory=list)
    note: str = ""


@ep_router.post("/episodes", status_code=201)
def create_episode(novel_id: str, data: EpisodeCreate):
    """手动新增分集（AI 大纲之外的补充入口）。"""
    _guard_novel(novel_id)
    return _run(script_mod.create_episode, novel_id, data.model_dump())


@ep_router.get("/episodes/{episode_id}")
def get_episode(novel_id: str, episode_id: str):
    """分集详情（含剧本全文与双方版本列表）。"""
    _guard_novel(novel_id)
    data = _run(script_mod.get_episode, novel_id, episode_id)
    if not data:
        raise HTTPException(404, "分集不存在")
    return data


class EpisodeUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    logline: Optional[str] = None
    target_duration_sec: Optional[int] = Field(None, ge=30, le=300)
    chapter_ids: Optional[list] = None
    beats: Optional[list] = None
    note: Optional[str] = None


@ep_router.put("/episodes/{episode_id}")
def update_episode(novel_id: str, episode_id: str, data: EpisodeUpdate):
    """编辑分集（字段白名单在数据层收敛；审核后改动自动回草稿态）。"""
    _guard_novel(novel_id)
    return _run(script_mod.update_episode, novel_id, episode_id,
                {k: v for k, v in data.model_dump(exclude_unset=True).items()})


@ep_router.delete("/episodes/{episode_id}", status_code=204)
def delete_episode(novel_id: str, episode_id: str):
    """删除分集（其剧本与版本随外键级联删除）。"""
    _guard_novel(novel_id)
    _run(script_mod.delete_episode, novel_id, episode_id)


class ApproveBody(BaseModel):
    note: str = ""


@ep_router.post("/episodes/{episode_id}/approve")
def approve_episode(novel_id: str, episode_id: str, data: ApproveBody):
    """审核通过：当前分集存为不可变版本快照，状态 → 已审核。"""
    _guard_novel(novel_id)
    return _run(script_mod.approve_episode, novel_id, episode_id, note=data.note)


@ep_router.get("/episodes/{episode_id}/versions/{version}")
def get_episode_version(novel_id: str, episode_id: str, version: int):
    """分集历史版本快照（不可变，可回看当时审核态）。"""
    _guard_novel(novel_id)
    return _run(script_mod.get_episode_version, novel_id, episode_id, version)


# =====================================================================
# 动漫剧本（生成 / 编辑 / 审核 / 交接）
# =====================================================================
@ep_router.post("/episodes/{episode_id}/script/generate")
def generate_script(novel_id: str, episode_id: str):
    """LLM 生成动漫剧本草案（工作台剧本格式；格式不符自动重试一次）。

    依赖故事圣经与分集章节引用；已有剧本则覆盖为新草案（回 draft）。
    """
    _guard_novel(novel_id)
    return _run(script_mod.generate_script, novel_id, episode_id)


@ep_router.get("/episodes/{episode_id}/script")
def get_script(novel_id: str, episode_id: str):
    """剧本全文 + 版本列表（未生成过 → 404）。"""
    _guard_novel(novel_id)
    return _run(script_mod.get_script, novel_id, episode_id)


class ScriptUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = None


@ep_router.put("/episodes/{episode_id}/script")
def update_script(novel_id: str, episode_id: str, data: ScriptUpdate):
    """编辑剧本（写入前用工作台解析器校验格式；审核后改动回草稿态）。"""
    _guard_novel(novel_id)
    return _run(script_mod.update_script, novel_id, episode_id,
                {k: v for k, v in data.model_dump(exclude_unset=True).items()})


@ep_router.post("/episodes/{episode_id}/script/approve")
def approve_script(novel_id: str, episode_id: str, data: ApproveBody):
    """审核剧本：格式校验 + 不可变版本快照，状态 → 已审核。"""
    _guard_novel(novel_id)
    return _run(script_mod.approve_script, novel_id, episode_id, note=data.note)


@ep_router.get("/episodes/{episode_id}/script/versions/{version}")
def get_script_version(novel_id: str, episode_id: str, version: int):
    """剧本历史版本快照（不可变）。"""
    _guard_novel(novel_id)
    return _run(script_mod.get_script_version, novel_id, episode_id, version)


@ep_router.post("/episodes/{episode_id}/script/handoff")
def handoff_script(novel_id: str, episode_id: str):
    """交接：审核通过的剧本 → 创建项目并导入场景分镜（幂等）。

    未审核 → 400；已交接 → 返回既有项目（不重复建项目）。
    """
    _guard_novel(novel_id)
    return _run(script_mod.handoff_script, novel_id, episode_id)
