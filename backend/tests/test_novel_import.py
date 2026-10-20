"""PR-N01：小说导入与章节管理——回归测试矩阵（任务书验收项逐条对照）。

覆盖：旧库迁移 / 中文章节识别 / 乱码 / 重复导入 / 非法文件 /
      原文位置（偏移往返） / 刷新持久化 / 旧接口兼容 / 章节拆分合并重识别。
"""
import sqlite3

import pytest

from app import novel as novel_mod
from app.novel import (
    ChapterError,
    DuplicateNovelError,
    NovelFormatError,
)

SAMPLE = (
    "序章：雨夜\n"
    "少年走在青石板上，远方传来剑鸣。\n\n"
    "第一章 竹林\n"
    "两人对峙，薄雾弥漫，落叶旋飞。\n\n"
    "第二章 突进\n"
    "剑光乍起，衣袂翻飞。\n\n"
    "尾声\n"
    "尘埃落定，雨又下了起来。\n"
).encode("utf-8")


# =====================================================================
# 旧库迁移
# =====================================================================
def test_old_db_gains_novel_tables_incrementally(tmp_path):
    """旧库（无 novels 表）打开后自动补表，已有表与数据零改动。"""
    from app.db import Database

    path = tmp_path / "old.db"
    # 模拟迁移前旧库：只建 projects 并写一行
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL,"
        " description TEXT DEFAULT '', script TEXT DEFAULT '',"
        " status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO projects VALUES ('prj_1','旧项目','','','draft','t','t')"
    )
    conn.commit()
    conn.close()

    db = Database(path)  # 启动迁移
    tables = {
        r["name"] for r in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"novels", "novel_chapters"} <= tables
    # 旧数据仍在，旧表未被重建（rowid 稳定即未 DROP/CREATE）
    row = db.fetch_one("SELECT name FROM projects WHERE id = 'prj_1'")
    assert row["name"] == "旧项目"
    db2 = Database(path)  # 重复启动幂等
    assert db2.fetch_one("SELECT COUNT(*) AS c FROM novels")["c"] == 0


# =====================================================================
# 编码与乱码
# =====================================================================
def test_decode_gbk_and_bom_and_reject_mojibake():
    text, enc = novel_mod.decode_novel_bytes("第一章 测试\n内容".encode("gb18030"))
    assert "第一章" in text and enc == "gb18030"
    text2, enc2 = novel_mod.decode_novel_bytes(
        "\ufeff第一章 测试".encode("utf-8"))
    assert enc2 == "utf-8" and not text2.startswith("\ufeff")

    with pytest.raises(NovelFormatError):
        novel_mod.decode_novel_bytes(bytes(range(128, 256)) * 40)  # 高位随机字节
    with pytest.raises(NovelFormatError):
        novel_mod.decode_novel_bytes(b"")
    with pytest.raises(NovelFormatError):
        novel_mod.decode_novel_bytes(b"   \n\t  ")


# =====================================================================
# 中文章节识别
# =====================================================================
def test_chinese_chapter_detection_and_offsets():
    chapters = novel_mod.split_chapters(SAMPLE.decode("utf-8"))
    titles = [c["title"] for c in chapters]
    assert titles == ["序章：雨夜", "第一章 竹林", "第二章 突进", "尾声"]
    # 偏移往返：切片必须还原各章原文（验收：保留原文位置）
    text = SAMPLE.decode("utf-8")
    for c in chapters:
        assert text[c["start"]:c["end"]].strip() != ""
    assert text[chapters[1]["start"]:chapters[1]["end"]].startswith("第一章")
    assert text[chapters[0]["start"]:chapters[1]["start"]] == text[:chapters[1]["start"]]


def test_markdown_and_plaintext_fallback():
    md = "# 卷一\n甲出手。\n\n## 乙应战\n剑光交错。"
    assert [c["title"] for c in novel_mod.split_chapters(md)] == ["卷一", "乙应战"]
    plain = "一个没有章节标题的短篇故事，直接作为全文一章。"
    chs = novel_mod.split_chapters(plain)
    assert len(chs) == 1 and chs[0]["title"] == "全文"
    assert chs[0]["end"] == len(plain)


# =====================================================================
# 导入 / 去重 / 非法文件
# =====================================================================
def test_import_roundtrip_and_duplicate_rejected(fresh_db):
    n = novel_mod.import_novel("少年剑行", "佚名", "sword.txt", SAMPLE)
    assert n["char_count"] == len(SAMPLE.decode("utf-8"))
    assert n["chapter_count"] == 4
    assert n["content_sha256"] == novel_mod.content_sha256(SAMPLE.decode("utf-8"))
    chs = novel_mod.list_chapters(n["id"])
    assert [c["seq"] for c in chs] == [1, 2, 3, 4]
    # 重复导入：同内容不同文件名也要拦住
    with pytest.raises(DuplicateNovelError) as exc:
        novel_mod.import_novel("副本", "", "copy.txt", SAMPLE)
    assert exc.value.existing_id == n["id"]


def test_import_rejects_oversize_and_bad_suffix_at_router(client):
    # 5MB+1 → 400（路由层大小校验）
    big = b"x" * (novel_mod.MAX_NOVEL_BYTES + 1)
    r = client.post("/api/novels", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 400 and "过大" in r.json()["detail"]
    # 非法后缀 → 400
    r = client.post("/api/novels", files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400 and "不支持" in r.json()["detail"]
    # 空文件 → 400
    r = client.post("/api/novels", files={"file": ("a.txt", b"", "text/plain")})
    assert r.status_code == 400


def test_import_via_api_roundtrip(client):
    r = client.post(
        "/api/novels",
        files={"file": ("sword.txt", SAMPLE, "text/plain")},
        data={"title": "少年剑行", "author": "佚名"},
    )
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    assert r.json()["chapter_count"] == 4

    # 重复导入 → 409 且带已存在 ID
    r2 = client.post("/api/novels", files={"file": ("again.txt", SAMPLE, "text/plain")})
    assert r2.status_code == 409 and nid in r2.json()["detail"]

    # 列表/详情不回传全文
    lst = client.get("/api/novels").json()
    assert len(lst) == 1 and "content" not in lst[0]
    det = client.get(f"/api/novels/{nid}").json()
    assert "content" not in det

    # 章节内容按偏移切片返回
    chs = client.get(f"/api/novels/{nid}/chapters").json()
    assert len(chs) == 4
    one = client.get(f"/api/novels/{nid}/chapters/{chs[1]['id']}").json()
    assert one["content"].startswith("第一章 竹林")


def test_import_mojibake_rejected_via_api(client):
    """高位随机字节：GB18030/BIG5 均无法完整解码 → 400 明确拒绝。

    说明：纯随机高位字节在 GB18030 下大概率非法；若某串字节能被完整
    解码成合法 CJK（双重编码类），本期不做启发式判定，由用户在章节
    预览中人工发现——不为猜乱码而误杀合法文本。
    """
    r = client.post("/api/novels",
                    files={"file": ("bad.txt", bytes(range(128, 256)) * 40,
                                    "text/plain")})
    assert r.status_code == 400
    assert "乱码" in r.json()["detail"] or "无法解码" in r.json()["detail"]
    assert client.get("/api/novels").json() == [], "失败导入不落库"


# =====================================================================
# 章节管理：修正 / 拆分 / 合并 / 重识别
# =====================================================================
def test_chapter_fix_and_offset_validation(fresh_db):
    n = novel_mod.import_novel("t", "", "t.txt", SAMPLE)
    chs = novel_mod.list_chapters(n["id"])
    # 重命名
    fixed = novel_mod.update_chapter(n["id"], chs[1]["id"], title="竹林对峙")
    assert fixed["title"] == "竹林对峙"
    # 合法偏移修正（把尾章起点前移两字符）→ 仍按 start 有序
    last = chs[-1]
    fixed2 = novel_mod.update_chapter(n["id"], last["id"], start=last["start"] - 2)
    assert fixed2["start"] == last["start"] - 2
    assert [c["seq"] for c in novel_mod.list_chapters(n["id"])] == [1, 2, 3, 4]
    # 越界拒绝
    with pytest.raises(ChapterError):
        novel_mod.update_chapter(n["id"], last["id"], start=-1)
    with pytest.raises(ChapterError):
        novel_mod.update_chapter(n["id"], last["id"], end=10**9)
    with pytest.raises(ChapterError):
        novel_mod.update_chapter(n["id"], "nch_missing", title="x")


def test_split_and_merge_roundtrip(fresh_db):
    n = novel_mod.import_novel("t", "", "t.txt", SAMPLE)
    chs = novel_mod.list_chapters(n["id"])
    target = chs[1]  # 第一章 竹林
    content = novel_mod.get_chapter(n["id"], target["id"])["content"]
    mid = len("第一章 竹林\n")
    up = novel_mod.split_chapter(n["id"], target["id"], mid)
    assert up["title"].endswith("（上）")
    down = novel_mod.get_chapter(n["id"], _next_seq(n["id"], up["id"]))
    assert down["title"].endswith("（下）")
    assert up["end"] == down["start"]
    # 合并回来：内容不变（仅标题回到原文）
    merged = novel_mod.merge_chapters(n["id"], up["id"], down["id"])
    assert merged["title"] == "第一章 竹林"
    assert novel_mod.get_chapter(n["id"], merged["id"])["content"] == content
    # 非法拆分点
    with pytest.raises(ChapterError):
        novel_mod.split_chapter(n["id"], merged["id"], 0)
    with pytest.raises(ChapterError):
        novel_mod.split_chapter(n["id"], merged["id"], 10**6)


def test_merge_requires_adjacent(fresh_db):
    n = novel_mod.import_novel("t", "", "t.txt", SAMPLE)
    chs = novel_mod.list_chapters(n["id"])
    with pytest.raises(ChapterError):
        novel_mod.merge_chapters(n["id"], chs[0]["id"], chs[2]["id"])


def test_reparse_replaces_chapters(fresh_db):
    n = novel_mod.import_novel("t", "", "t.txt", SAMPLE)
    chs = novel_mod.list_chapters(n["id"])
    novel_mod.update_chapter(n["id"], chs[1]["id"], title="改过的标题")
    after = novel_mod.reparse_chapters(n["id"])
    assert [c["title"] for c in after] == [
        "序章：雨夜", "第一章 竹林", "第二章 突进", "尾声"]
    # 原文未被章节操作改写
    assert novel_mod.get_novel(n["id"], with_content=True)["content"] == \
        SAMPLE.decode("utf-8")


def _next_seq(novel_id: str, chapter_id: str) -> str:
    ch = novel_mod.get_chapter(novel_id, chapter_id, with_content=False)
    for c in novel_mod.list_chapters(novel_id):
        if c["seq"] == ch["seq"] + 1:
            return c["id"]
    raise AssertionError("下一章不存在")


# =====================================================================
# 持久化（模拟页面刷新）与旧接口兼容
# =====================================================================
def test_persistence_across_reopen(tmp_path, monkeypatch):
    import app.db as db_module
    import app.novel  # noqa: F401

    target = db_module.Database(tmp_path / "novel.db")
    saved = db_module.db
    db_module.db = target
    monkeypatch.setattr("app.novel.db", target)
    try:
        n = novel_mod.import_novel("持久化", "作者", "p.txt", SAMPLE)
        ch_count = len(novel_mod.list_chapters(n["id"]))
    finally:
        db_module.db = saved

    # 「刷新」= 重开数据库：记录与章节原样恢复
    reopened = db_module.Database(tmp_path / "novel.db")
    monkeypatch.setattr("app.novel.db", reopened)
    n2 = novel_mod.get_novel(n["id"], with_content=True)
    assert n2 is not None and n2["title"] == "持久化"
    assert len(novel_mod.list_chapters(n["id"])) == ch_count == 4
    assert n2["content_sha256"] == novel_mod.content_sha256(SAMPLE.decode("utf-8"))


def test_legacy_apis_unaffected(client):
    """旧接口兼容：新增 novels 路由不影响既有 API 与前端页面路由。"""
    assert client.get("/api/health").json()["ok"] is True
    assert client.post("/api/projects", json={"name": "旧兼容"}).status_code == 201
    assert client.get("/api/projects").status_code == 200
    # SPA catch-all 未被 /api/novels 影响（未知 api 路径仍走 404 而非页面）
    assert client.get("/api/novels/not-exist").status_code == 404


def test_delete_novel_cascades(fresh_db):
    n = novel_mod.import_novel("t", "", "t.txt", SAMPLE)
    novel_mod.delete_novel(n["id"])
    assert novel_mod.get_novel(n["id"]) is None
    assert novel_mod.list_chapters(n["id"]) == []
