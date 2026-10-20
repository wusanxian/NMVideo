"""PR-N01：小说改编流水线——原文导入与章节管理（前期制作数据层）。

职责边界（第一期）：
- 小说文件导入：编码探测 / 乱码拒绝 / 哈希去重 / 原文入库
- 章节管理：自动识别（中文章节 + Markdown 兜底）、保留原文位置（start/end 偏移）、
  查看 / 重命名 / 拆分 / 合并 / 重新识别
- 不调用 LLM、不触发视频生成——故事分析与分镜是后续 PR-N02~N04 的职责

设计约定：
- 全文存 novels.content（SQLite TEXT 可承载 MB 级网文）；章节只存偏移切片，
  「拆分/合并/重命名」不回写正文，原文哈希保持导入时指纹（可追溯）。
- 所有写操作走 db.transaction() 单事务；读走 db 统一接口。
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import db

# 上传校验（路由层同样校验，双保险）
MAX_NOVEL_BYTES = 5 * 1024 * 1024  # 5MB：足够 200 万字 UTF-8，防异常大文件拖垮内存
ALLOWED_SUFFIXES = (".txt", ".md")

# 解码尝试顺序：utf-8-sig 先吞 BOM；gb18030 是 GBK 超集（简繁兼容）；
# big5 兜底繁体 txt。不做 latin-1 兜底——那会把乱码洗成合法文本蒙混过关。
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5")

# 中文章节标题：第X章/回/节/卷/部/篇（X 支持中文数字）、序章/序/楔子/尾声/
# 终章/番外、Chapter N / 第N话。标题行内允许前后空白与常见装饰符号。
_CHAPTER_RE = re.compile(
    r"^[ \t　]*(?:"
    r"第[0-9零一二三四五六七八九十百千两]+[章回节卷部篇话]"
    r"|序[章曲]?|楔子|尾声|终章|番外[篇卷]?"
    r"|Chapter\s*\d+|CHAPTER\s*\d+"
    r")[ \t　]*(.*?)[ \t　]*$",
    re.MULTILINE,
)

# Markdown 兜底：仅当全文没有任何中文章节标题时启用（.md 的结构化标题）
_MD_HEADING_RE = re.compile(r"^#{1,2}[ \t]+(.+?)[ \t]*$", re.MULTILINE)

_NOVEL_STATUSES = ("imported", "analyzed")


class NovelFormatError(ValueError):
    """文件无法作为小说导入：空文件 / 无法解码 / 乱码比例过高"""


class DuplicateNovelError(ValueError):
    """内容哈希已存在（重复导入）"""

    def __init__(self, message: str, existing_id: str = ""):
        super().__init__(message)
        self.existing_id = existing_id


class ChapterError(ValueError):
    """章节操作非法：不存在 / 偏移越界 / 非相邻合并 / 拆分点无效"""


# =====================================================================
# 编码与哈希
# =====================================================================
def decode_novel_bytes(data: bytes) -> tuple:
    """bytes → (text, encoding)。失败抛 NovelFormatError。

    乱码判定：解码结果中 U+FFFD（替换字符）占比 > 1% 视为乱码拒绝——
    GBK/UTF-8 混拷文件是网文最常见的事故，宁可拒绝也不要带着乱钥进库。
    """
    if not data:
        raise NovelFormatError("文件内容为空")
    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if not text.strip():
            raise NovelFormatError("文件内容只有空白字符")
        bad = text.count("\ufffd")
        if bad and bad / max(len(text), 1) > 0.01:
            raise NovelFormatError(
                f"文件疑似乱码（{enc} 解码后替换字符占比过高），"
                "请确认文件为 UTF-8 / GBK 编码的纯文本"
            )
        # utf-8-sig 解码成功即吞掉 BOM；统一回报规范编码名
        return text.replace("\r\r\n", "\n").replace("\r\n", "\n"), (
            "utf-8" if enc == "utf-8-sig" else enc
        )
    raise NovelFormatError(
        "无法解码文件（非 UTF-8 / GBK / BIG5 文本），请转换为纯文本后重试"
    )


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# =====================================================================
# 章节识别（保留原文位置）
# =====================================================================
def _normalize_title(raw: str, fallback: str) -> str:
    title = (raw or "").strip(" \t　-—:：·.。[]【】")
    return title or fallback


def split_chapters(text: str) -> list:
    """全文 → [{title, start, end}]，保留原文偏移。

    策略：
    1. 中文章节正则（第X章/序章/楔子/尾声/番外/Chapter N）
    2. 无命中 → Markdown ATX 标题兜底（.md 结构）
    3. 仍无 → 单章「全文」（短篇/无标题文本不阻断导入）
    章节区间从标题行起点到下一标题行起点，正文零改动。
    """
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        matches = list(_MD_HEADING_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        if not stripped:
            raise NovelFormatError("文件内容为空")
        return [{"title": "全文", "start": 0, "end": len(text)}]

    chapters = []
    md_fallback = not list(_CHAPTER_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if end <= start:
            continue
        # 标题：中文章节取整行（「序章：雨夜」不退化成「雨夜」）；
        # Markdown 兜底只取 ## 之后文本（不带井号）。仅剥装饰符号。
        raw = m.group(1) if md_fallback else text[start:m.end()]
        title = _normalize_title(raw, "")
        chapters.append({"title": title, "start": start, "end": end})
    if not chapters:  # 防御：极端输入（全是空标题行）退化为全文
        return [{"title": "全文", "start": 0, "end": len(text)}]
    return chapters


# =====================================================================
# 导入 / 查询
# =====================================================================
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _row(sql: str, args: tuple = ()) -> Optional[dict]:
    return db.fetch_one(sql, args)


def _rows(sql: str, args: tuple = ()) -> list:
    return db.fetch_all(sql, args)


def _write(sql: str, args: tuple = ()):
    db.execute(sql, args)


def import_novel(title: str, author: str, filename: str, data: bytes) -> dict:
    """导入小说：解码 → 哈希 → 去重 → 原文+章节单事务入库。"""
    text, encoding = decode_novel_bytes(data)
    digest = content_sha256(text)
    dup = _row("SELECT id, title FROM novels WHERE content_sha256 = ?", (digest,))
    if dup:
        raise DuplicateNovelError(
            f"该小说已导入过（《{dup['title']}》，ID {dup['id']}），重复导入已阻止",
            existing_id=dup["id"],
        )
    chapters = split_chapters(text)
    nid = _new_id("nov")
    now = _utcnow()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO novels (id, title, author, source_filename, content,"
            " content_sha256, encoding, char_count, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (nid, title.strip() or "未命名小说", (author or "").strip(), filename or "",
             text, digest, encoding, len(text), "imported", now, now),
        )
        for seq, ch in enumerate(chapters, 1):
            conn.execute(
                'INSERT INTO novel_chapters (id, novel_id, seq, title, start, "end",'
                " char_count, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (_new_id("nch"), nid, seq, ch["title"], ch["start"], ch["end"],
                 ch["end"] - ch["start"], now),
            )
    return get_novel(nid)


def get_novel(novel_id: str, with_content: bool = False) -> Optional[dict]:
    row = _row("SELECT * FROM novels WHERE id = ?", (novel_id,))
    if not row:
        return None
    novel = dict(row)
    novel["chapter_count"] = _row(
        "SELECT COUNT(*) AS c FROM novel_chapters WHERE novel_id = ?", (novel_id,)
    )["c"]
    if not with_content:
        novel.pop("content", None)  # 列表/详情默认不回传全文，避免大 payload
    return novel


def list_novels() -> list:
    """小说列表（不回传全文 content，避免大 payload）。"""
    novels = _rows("SELECT * FROM novels ORDER BY updated_at DESC")
    return [
        {k: v for k, v in dict(n).items() if k != "content"}
        for n in novels
    ]


def update_novel(novel_id: str, *, title: str = None, author: str = None) -> Optional[dict]:
    fields, args = [], []
    if title is not None:
        fields.append("title = ?")
        args.append(title.strip() or "未命名小说")
    if author is not None:
        fields.append("author = ?")
        args.append(author.strip())
    if not fields:
        return get_novel(novel_id)
    fields.append("updated_at = ?")
    args.append(_utcnow())
    args.append(novel_id)
    _write(f"UPDATE novels SET {', '.join(fields)} WHERE id = ?", tuple(args))
    return get_novel(novel_id)


def delete_novel(novel_id: str) -> None:
    """删除小说（章节随外键 CASCADE；不影响任何项目/镜头/成片）。"""
    _write("DELETE FROM novels WHERE id = ?", (novel_id,))


# =====================================================================
# 章节管理
# =====================================================================
def list_chapters(novel_id: str) -> list:
    return _rows(
        'SELECT id, novel_id, seq, title, start, "end" AS end, char_count'
        " FROM novel_chapters WHERE novel_id = ? ORDER BY seq", (novel_id,)
    )


def get_chapter(novel_id: str, chapter_id: str, *, with_content: bool = True) -> Optional[dict]:
    ch = _row(
        'SELECT id, novel_id, seq, title, start, "end" AS end, char_count'
        " FROM novel_chapters WHERE id = ? AND novel_id = ?", (chapter_id, novel_id)
    )
    if not ch:
        return None
    ch = dict(ch)
    if with_content:
        novel = _row("SELECT content FROM novels WHERE id = ?", (novel_id,))
        ch["content"] = (novel or {}).get("content", "")[ch["start"]:ch["end"]]
    return ch


def _reseq(conn, novel_id: str) -> None:
    rows = conn.execute(
        'SELECT id FROM novel_chapters WHERE novel_id = ? ORDER BY start, seq',
        (novel_id,),
    ).fetchall()
    for seq, r in enumerate(rows, 1):
        conn.execute("UPDATE novel_chapters SET seq = ? WHERE id = ?", (seq, r["id"]))


def update_chapter(novel_id: str, chapter_id: str, *, title: str = None,
                   start: int = None, end: int = None) -> dict:
    """修正章节：标题 / 起止偏移（偏移需在原文范围内且 start < end）。

    偏移修正后按 start 重排序号（用户手工纠偏是预期操作）。
    """
    ch = get_chapter(novel_id, chapter_id, with_content=False)
    if not ch:
        raise ChapterError("章节不存在")
    novel = _row("SELECT content FROM novels WHERE id = ?", (novel_id,))
    total = len((novel or {}).get("content", ""))
    new_title = ch["title"] if title is None else (title.strip() or ch["title"])
    new_start = ch["start"] if start is None else int(start)
    new_end = ch["end"] if end is None else int(end)
    if not (0 <= new_start < new_end <= total):
        raise ChapterError(
            f"章节偏移越界：要求 0 <= start < end <= {total}，"
            f"收到 [{new_start}, {new_end}]"
        )
    with db.transaction() as conn:
        conn.execute(
            'UPDATE novel_chapters SET title = ?, start = ?, "end" = ?,'
            " char_count = ? WHERE id = ?",
            (new_title, new_start, new_end, new_end - new_start, chapter_id),
        )
        _reseq(conn, novel_id)
    return get_chapter(novel_id, chapter_id, with_content=False)


def split_chapter(novel_id: str, chapter_id: str, offset: int) -> dict:
    """在章节正文 offset 处拆成两章（标题追加（上）/（下））。

    offset 为章节内相对位置；拆分点落在标题行首视为无效。
    """
    ch = get_chapter(novel_id, chapter_id, with_content=False)
    if not ch:
        raise ChapterError("章节不存在")
    length = ch["end"] - ch["start"]
    offset = int(offset)
    if not (1 <= offset < length - 1):
        raise ChapterError(
            f"拆分点无效：须在章节内 1 ~ {length - 2} 之间（收到 {offset}）"
        )
    boundary = ch["start"] + offset
    now = _utcnow()
    with db.transaction() as conn:
        conn.execute(
            'UPDATE novel_chapters SET title = ?, "end" = ?, char_count = ?'
            " WHERE id = ?",
            (f"{ch['title']}（上）", boundary, offset, chapter_id),
        )
        conn.execute(
            'INSERT INTO novel_chapters (id, novel_id, seq, title, start, "end",'
            " char_count, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (_new_id("nch"), novel_id, ch["seq"] + 1, f"{ch['title']}（下）",
             boundary, ch["end"], ch["end"] - boundary, now),
        )
        _reseq(conn, novel_id)
    return get_chapter(novel_id, chapter_id, with_content=False)


def merge_chapters(novel_id: str, chapter_id: str, next_chapter_id: str) -> dict:
    """合并两个相邻章节（前者吸收后者；标题取前者，偏移取并集）。"""
    a = get_chapter(novel_id, chapter_id, with_content=False)
    b = get_chapter(novel_id, next_chapter_id, with_content=False)
    if not a or not b:
        raise ChapterError("章节不存在")
    if b["seq"] != a["seq"] + 1:
        raise ChapterError(
            f"只能合并相邻章节（前者 seq={a['seq']}，后者 seq={b['seq']}）"
        )
    # 一对拆分产生的（上）/（下）合并时还原原标题（可逆操作）
    merged_title = a["title"]
    if a["title"].endswith("（上）") and b["title"] == \
            a["title"].removesuffix("（上）") + "（下）":
        merged_title = a["title"].removesuffix("（上）")
    with db.transaction() as conn:
        conn.execute(
            'UPDATE novel_chapters SET title = ?, "end" = ?, char_count = ?'
            " WHERE id = ?",
            (merged_title, b["end"], b["end"] - a["start"], chapter_id),
        )
        conn.execute("DELETE FROM novel_chapters WHERE id = ?", (next_chapter_id,))
        _reseq(conn, novel_id)
    return get_chapter(novel_id, chapter_id, with_content=False)


def reparse_chapters(novel_id: str) -> list:
    """按当前全文重新识别章节（替换既有章节，保留原文不动）。"""
    novel = _row("SELECT content FROM novels WHERE id = ?", (novel_id,))
    if not novel:
        raise ChapterError("小说不存在")
    chapters = split_chapters(novel["content"])
    now = _utcnow()
    with db.transaction() as conn:
        conn.execute("DELETE FROM novel_chapters WHERE novel_id = ?", (novel_id,))
        for seq, ch in enumerate(chapters, 1):
            conn.execute(
                'INSERT INTO novel_chapters (id, novel_id, seq, title, start, "end",'
                " char_count, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (_new_id("nch"), novel_id, seq, ch["title"], ch["start"], ch["end"],
                 ch["end"] - ch["start"], now),
            )
    return list_chapters(novel_id)
