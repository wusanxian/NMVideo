"""PR-N02：故事圣经 REST API——章节分析（AI 理解）+ Bible 实体管理与审核版本。

职责边界：只做「章节 → 结构化设定」的前期制作数据；不触发视频生成、
不触碰项目/镜头/成片。所有 AI 产出均可编辑、可审核、可保存版本。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import novel as novel_mod
from .. import novel_bible as bible_mod

router = APIRouter(prefix="/api", tags=["novel-bible"])
bible_router = APIRouter(prefix="/api/novels/{novel_id}/bible", tags=["novel-bible"])


def _guard_novel(novel_id: str) -> None:
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")


def _bible_error(e: bible_mod.BibleError) -> HTTPException:
    return HTTPException(e.status, str(e))


# =====================================================================
# 章节分析（AI 分章节理解）
# =====================================================================
class AnalyzeStart(BaseModel):
    chapter_ids: Optional[list] = Field(None, description="待分析章节 ID；空=全部")
    force: bool = Field(False, description="强制重新分析（含已完成章节）")


@router.post("/novels/{novel_id}/analyze")
def start_analyze(novel_id: str, data: AnalyzeStart):
    """启动 AI 章节分析（后台线程，立即返回）。

    默认跳过已完成章节；force=True 重跑。LLM 未配置 → 400 引导去设置页；
    同一小说分析中 → 409。
    """
    _guard_novel(novel_id)
    try:
        return bible_mod.start_analysis(
            novel_id, chapter_ids=data.chapter_ids, force=data.force
        )
    except bible_mod.BibleError as e:
        raise _bible_error(e) from e


@router.get("/novels/{novel_id}/analyze")
def analyze_progress(novel_id: str):
    """分析进度（前端轮询）：总数/完成/失败/进行中 + 逐章状态。"""
    _guard_novel(novel_id)
    return bible_mod.analysis_status(novel_id)


# =====================================================================
# 故事圣经：生成 / 查询 / 审核
# =====================================================================
@bible_router.get("")
def get_bible(novel_id: str):
    """完整圣经（头部 + 角色/场景/时间线/关系 + 版本列表）。未生成过 → 404。"""
    _guard_novel(novel_id)
    data = bible_mod.get_bible(novel_id)
    if not data:
        raise HTTPException(404, "故事圣经尚未生成：请先运行章节分析并「生成故事圣经」")
    return data


@bible_router.post("/generate")
def generate_bible(novel_id: str):
    """汇总已完成章节分析 → 生成/刷新圣经草案（实体被替换，版本历史保留）。"""
    _guard_novel(novel_id)
    try:
        return bible_mod.generate_bible(novel_id)
    except bible_mod.BibleError as e:
        raise _bible_error(e) from e


class ApproveBody(BaseModel):
    note: str = ""


@bible_router.post("/approve")
def approve_bible(novel_id: str, data: ApproveBody):
    """审核通过：当前实体存为不可变版本快照，状态 → 已审核。"""
    _guard_novel(novel_id)
    try:
        return bible_mod.approve_bible(novel_id, note=data.note)
    except bible_mod.BibleError as e:
        raise _bible_error(e) from e


@bible_router.get("/versions/{version}")
def get_version(novel_id: str, version: int):
    """历史版本快照（不可变，可回看当时审核态）。"""
    _guard_novel(novel_id)
    try:
        return bible_mod.get_version(novel_id, version)
    except bible_mod.BibleError as e:
        raise _bible_error(e) from e


# =====================================================================
# 实体管理（角色/场景/时间线/关系）：增 / 改 / 删
# =====================================================================
_ENTITY_LABELS = {
    "characters": "角色", "scenes": "场景",
    "timeline": "时间线", "relations": "人物关系",
}


def _entity_router(kind: str, path: str):
    """为四类实体生成 CRUD 子路由（字段白名单在 novel_bible 层收敛）。"""

    @bible_router.post(path, status_code=201)
    def create(novel_id: str, fields: dict):
        _guard_novel(novel_id)
        try:
            return bible_mod.create_entity(kind, novel_id, fields or {})
        except bible_mod.BibleError as e:
            raise _bible_error(e) from e

    @bible_router.put(path + "/{entity_id}")
    def update(novel_id: str, entity_id: str, fields: dict):
        _guard_novel(novel_id)
        try:
            return bible_mod.update_entity(kind, novel_id, entity_id, fields or {})
        except bible_mod.BibleError as e:
            raise _bible_error(e) from e

    @bible_router.delete(path + "/{entity_id}", status_code=204)
    def remove(novel_id: str, entity_id: str):
        _guard_novel(novel_id)
        try:
            bible_mod.delete_entity(kind, novel_id, entity_id)
        except bible_mod.BibleError as e:
            raise _bible_error(e) from e


for _kind, _path in (
    ("characters", "/characters"),
    ("scenes", "/scenes"),
    ("timeline", "/timeline"),
    ("relations", "/relations"),
):
    _entity_router(_kind, _path)
