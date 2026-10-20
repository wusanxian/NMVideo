"""PR-N01：小说改编流水线 REST API——导入 / 章节管理。

职责边界：只做「小说 → 可编辑章节」的前期制作数据；不调用 LLM、
不触发视频生成、不触碰项目/镜头/成片。故事分析与自动分镜在
PR-N02~N04，届时以 chapters 为输入。
"""
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import novel as novel_mod
from ..config import OUTPUTS_DIR

router = APIRouter(prefix="/api", tags=["novels"])

NOVELS_UPLOAD_DIR = OUTPUTS_DIR / "uploads" / "novels"


# =====================================================================
# 模型
# =====================================================================
class NovelUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None


class ChapterUpdate(BaseModel):
    title: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None


class ChapterSplit(BaseModel):
    offset: int = Field(..., description="章节内拆分位置（相对本章起点）")


class ChapterMerge(BaseModel):
    chapter_id: str = Field(..., description="前一章节 ID")
    next_chapter_id: str = Field(..., description="后一相邻章节 ID")


# =====================================================================
# 导入
# =====================================================================
@router.post("/novels", status_code=201)
async def import_novel(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
):
    """上传 TXT/Markdown 小说：校验 → 解码 → 哈希去重 → 章节识别 → 入库。

    失败语义：400 格式/大小/乱码；409 内容重复（带已存在小说 ID）。
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in novel_mod.ALLOWED_SUFFIXES:
        raise HTTPException(
            400, f"不支持的文件类型 {suffix or '（无扩展名）'}"
                f"（第一期支持：{'、'.join(novel_mod.ALLOWED_SUFFIXES)}）"
        )
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件内容为空")
    if len(data) > novel_mod.MAX_NOVEL_BYTES:
        raise HTTPException(
            400, f"文件过大（{len(data) / 1024 / 1024:.1f}MB > "
                f"{novel_mod.MAX_NOVEL_BYTES / 1024 / 1024:.0f}MB）"
        )
    # 保留上传原件（溯源用；权威全文以数据库为准）
    NOVELS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved = NOVELS_UPLOAD_DIR / f"{uuid.uuid4().hex[:10]}{suffix}"
    saved.write_bytes(data)

    try:
        return novel_mod.import_novel(
            title=title, author=author, filename=file.filename or "", data=data
        )
    except novel_mod.DuplicateNovelError as e:
        saved.unlink(missing_ok=True)
        raise HTTPException(409, str(e)) from e
    except novel_mod.NovelFormatError as e:
        saved.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e


# =====================================================================
# 查询 / 维护
# =====================================================================
@router.get("/novels")
def list_novels():
    return novel_mod.list_novels()


@router.get("/novels/{novel_id}")
def get_novel(novel_id: str):
    n = novel_mod.get_novel(novel_id)
    if not n:
        raise HTTPException(404, "小说不存在")
    return n


@router.patch("/novels/{novel_id}")
def update_novel(novel_id: str, data: NovelUpdate):
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    try:
        return novel_mod.update_novel(
            novel_id,
            title=data.title if data.title is not None else None,
            author=data.author if data.author is not None else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/novels/{novel_id}", status_code=204)
def delete_novel(novel_id: str):
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    novel_mod.delete_novel(novel_id)


# =====================================================================
# 章节
# =====================================================================
@router.get("/novels/{novel_id}/chapters")
def list_chapters(novel_id: str):
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    return novel_mod.list_chapters(novel_id)


@router.get("/novels/{novel_id}/chapters/{chapter_id}")
def get_chapter(novel_id: str, chapter_id: str):
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    ch = novel_mod.get_chapter(novel_id, chapter_id)
    if not ch:
        raise HTTPException(404, "章节不存在")
    return ch


@router.patch("/novels/{novel_id}/chapters/{chapter_id}")
def update_chapter(novel_id: str, chapter_id: str, data: ChapterUpdate):
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    try:
        return novel_mod.update_chapter(
            novel_id, chapter_id,
            title=data.title,
            start=data.start if data.start is not None else None,
            end=data.end if data.end is not None else None,
        )
    except novel_mod.ChapterError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/novels/{novel_id}/chapters/{chapter_id}/split")
def split_chapter(novel_id: str, chapter_id: str, data: ChapterSplit):
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    try:
        return novel_mod.split_chapter(novel_id, chapter_id, data.offset)
    except novel_mod.ChapterError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/novels/{novel_id}/chapters/merge")
def merge_chapters(novel_id: str, data: ChapterMerge):
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    try:
        return novel_mod.merge_chapters(novel_id, data.chapter_id, data.next_chapter_id)
    except novel_mod.ChapterError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/novels/{novel_id}/reparse")
def reparse(novel_id: str):
    """按当前全文重新识别章节（替换既有章节，原文不动）。"""
    if not novel_mod.get_novel(novel_id):
        raise HTTPException(404, "小说不存在")
    try:
        return novel_mod.reparse_chapters(novel_id)
    except novel_mod.ChapterError as e:
        raise HTTPException(400, str(e)) from e
