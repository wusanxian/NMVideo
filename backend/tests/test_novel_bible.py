"""PR-N02：故事圣经回归测试（LLM 全 mock，不触网）。

覆盖：章节分析（成功/乱码输出重试后失败/并发 409/强制重跑/跳过已完成）、
本地合并 + LLM 合并建议、实体 CRUD、章节定位快照、审核版本不可变、
重新生成保留版本、重识别不抹分析、删除小说级联、旧接口兼容。
"""
import json
import re
import threading
import time

import pytest

from app import llm as llm_mod
from app import settings as settings_mod

# 两章测试小说（标题即 fake LLM 的分发键）
NOVEL_TEXT = (
    "第一章 竹林\n"
    "少年走进竹林，薄雾弥漫。白衣少女按剑而立，两人对峙。\n\n"
    "第二章 突进\n"
    "少年拔剑突进，少女横剑格挡。灰袍长老现身喝止：住手！\n"
)

CH1_RESP = {
    "summary": "少年竹林遇白衣少女，双方对峙",
    "characters": [
        {"name": "少年", "aliases": [], "role": "主角", "appearance": "青衫",
         "clothing": "青衫", "personality": "沉稳", "catchphrase": ""},
        {"name": "白衣少女", "aliases": [], "role": "主角", "appearance": "白衣",
         "clothing": "白裙", "personality": "冷傲", "catchphrase": ""},
    ],
    "scenes": [{"name": "竹林", "description": "薄雾青竹", "atmosphere": "清冷"}],
    "events": [{"title": "竹林对峙", "summary": "两人竹林相遇对峙",
                "characters": ["少年", "白衣少女"]}],
    "relations": [{"from": "少年", "to": "白衣少女", "type": "敌对",
                   "description": "立场对立"}],
}

CH2_RESP = {
    "summary": "少年拔剑突进被长老喝止",
    "characters": [
        {"name": "林川", "aliases": [], "role": "主角", "appearance": "青衫",
         "clothing": "青衫", "personality": "沉稳", "catchphrase": ""},
        {"name": "苏檸", "aliases": ["白衣少女"], "role": "主角", "appearance": "白衣",
         "clothing": "白裙", "personality": "冷傲", "catchphrase": ""},
        {"name": "长老", "aliases": [], "role": "配角", "appearance": "灰袍老者",
         "clothing": "灰袍", "personality": "威严", "catchphrase": "住手！"},
    ],
    "scenes": [{"name": "竹林", "description": "薄雾青竹，剑气纵横",
                "atmosphere": "清冷"}],
    "events": [
        {"title": "拔剑突进", "summary": "少年拔剑突进，少女格挡",
         "characters": ["林川", "苏檸"]},
        {"title": "长老喝止", "summary": "长老现身喝止两人",
         "characters": ["林川", "苏檸", "长老"]},
    ],
    "relations": [],
}

MERGE_RESP = {
    "merges": [
        {"canonical": "林川", "absorb": ["少年"]},
        {"canonical": "苏檸", "absorb": ["白衣少女"]},
    ],
    "summary": "青衫少年林川与白衣剑客苏檸竹林对峙，拔剑突进之际被长老喝止。",
}


def _patch_llm(monkeypatch, tmp_path, *, chapters=None, merge=None, on_chapter=None,
               configured=True):
    """mock LLM：按 system prompt 分发（章节分析 / 合并建议）。

    chapters: {章节标题: 响应（str 或 Exception）}，缺省返回 CH1/CH2 默认 JSON；
    merge: 合并建议响应；on_chapter: 章节分析回调（阻塞测试用）。
    configured=False 时模拟「未配置 LLM」（不 patch is_configured）。
    """
    if configured:
        cfg = tmp_path / "settings.json"
        cfg.write_text(json.dumps(
            {"llm": {"base_url": "http://llm.test", "model": "test-model"}},
            ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(settings_mod, "SETTINGS_FILE", cfg)

    def fake_chat(messages, **kwargs):
        system = messages[0]["content"]
        if "设定整理专家" in system:
            if isinstance(merge, Exception):
                raise merge
            return merge if merge is not None else json.dumps(
                MERGE_RESP, ensure_ascii=False)
        # 章节标题从任意一条消息里找（重试调用时原 user 消息仍在列表里）
        title = ""
        for msg in messages:
            m = re.search(r"【章节标题】(.*)", msg.get("content", ""))
            if m:
                title = m.group(1).split("\n")[0].strip()
                break
        if on_chapter:
            on_chapter()
        resp = (chapters or {}).get(title)
        if isinstance(resp, Exception):
            raise resp
        if resp is None:
            default = CH1_RESP if title.startswith("第一章") else CH2_RESP
            resp = json.dumps(default, ensure_ascii=False)
        return resp

    if configured:
        monkeypatch.setattr(llm_mod, "chat", fake_chat)
        monkeypatch.setattr(llm_mod, "is_configured", lambda: True)


def _import(client):
    r = client.post("/api/novels",
                    files={"file": ("test.txt", NOVEL_TEXT.encode("utf-8"),
                                    "text/plain")},
                    data={"title": "竹林测试"})
    assert r.status_code == 201, r.text
    return r.json()


def _wait_analysis(client, nid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/novels/{nid}/analyze").json()
        if not st["running"] and st["pending"] == 0:
            return st
        time.sleep(0.05)
    raise AssertionError("分析未在超时时间内完成")


# =====================================================================
# 分析 → 生成 → 定位
# =====================================================================
def test_analyze_generate_and_traceability(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    chapters = client.get(f"/api/novels/{nid}/chapters").json()
    assert [c["title"] for c in chapters] == ["第一章 竹林", "第二章 突进"]

    r = client.post(f"/api/novels/{nid}/analyze", json={})
    assert r.status_code == 200 and r.json()["started"] == 2, r.text
    st = _wait_analysis(client, nid)
    assert st["done"] == 2 and st["error"] == 0, st

    r = client.post(f"/api/novels/{nid}/bible/generate")
    assert r.status_code == 200, r.text
    bible = r.json()
    names = {c["name"] for c in bible["characters"]}
    # LLM 合并建议生效：少年→林川、白衣少女→苏檸
    assert names == {"林川", "苏檸", "长老"}, names
    lin = next(c for c in bible["characters"] if c["name"] == "林川")
    assert "少年" in lin["aliases"]
    # 章节定位快照：两章都出现，且与当前章节行一致
    assert [r["chapter_id"] for r in lin["chapters"]] == [c["id"] for c in chapters]
    ref0 = lin["chapters"][0]
    assert ref0["title"] == chapters[0]["title"]
    assert ref0["start"] == chapters[0]["start"] and ref0["end"] == chapters[0]["end"]
    elder = next(c for c in bible["characters"] if c["name"] == "长老")
    assert [r["chapter_id"] for r in elder["chapters"]] == [chapters[1]["id"]]
    # 时间线：三起事件按章排序，人物名随合并重映射
    titles = [e["title"] for e in bible["timeline"]]
    assert titles == ["竹林对峙", "拔剑突进", "长老喝止"], titles
    assert "林川" in bible["timeline"][0]["character_names"]
    assert "少年" not in bible["timeline"][0]["character_names"]
    # 关系两端重映射
    assert len(bible["relations"]) == 1
    rel = bible["relations"][0]
    assert (rel["from_character"], rel["to_character"], rel["relation_type"]) == \
        ("林川", "苏檸", "敌对")
    # 场景跨章合并 + 总纲来自 LLM 合并响应
    assert len(bible["scenes"]) == 1
    assert len(bible["scenes"][0]["chapters"]) == 2
    assert bible["bible"]["summary"] == MERGE_RESP["summary"]
    assert bible["bible"]["status"] == "draft"


def test_bible_404_before_generate(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    r = client.get(f"/api/novels/{n['id']}/bible")
    assert r.status_code == 404
    r = client.post(f"/api/novels/{n['id']}/bible/generate")
    assert r.status_code == 400 and "章节分析" in r.json()["detail"]


def test_analyze_requires_llm_config(client, fresh_db, monkeypatch, tmp_path):
    # 不 patch is_configured；settings 指向不存在的文件 → 未配置
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", tmp_path / "none.json")
    n = _import(client)
    r = client.post(f"/api/novels/{n['id']}/analyze", json={})
    assert r.status_code == 400
    assert "模型服务" in r.json()["detail"]


def test_analyze_skips_done_and_force_reruns(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    _wait_analysis(client, nid)

    r = client.post(f"/api/novels/{nid}/analyze", json={})
    assert r.status_code == 400 and "都已分析过" in r.json()["detail"]

    r = client.post(f"/api/novels/{nid}/analyze", json={"force": True})
    assert r.status_code == 200 and r.json()["started"] == 2
    st = _wait_analysis(client, nid)
    assert st["done"] == 2 and st["error"] == 0


def test_analyze_invalid_json_marks_error_only(client, fresh_db, monkeypatch, tmp_path):
    # 第一章初始与重试都返回非 JSON → 该章 error；第二章正常 → done
    _patch_llm(monkeypatch, tmp_path, chapters={
        "第一章 竹林": "抱歉，我不能处理这个请求。",
    })
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    st = _wait_analysis(client, nid)
    assert st["done"] == 1 and st["error"] == 1
    items = {it["chapter_title"]: it for it in st["items"]}
    assert items["第一章 竹林"]["status"] == "error"
    assert "JSON" in items["第一章 竹林"]["error"]
    assert items["第二章 突进"]["status"] == "done"


def test_analyze_concurrent_returns_409(client, fresh_db, monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    state = {"n": 0}

    def on_chapter():
        state["n"] += 1
        if state["n"] == 1:  # 只阻塞第一次调用，放行后续章节
            entered.set()
            assert release.wait(15), "测试等待释放超时"

    _patch_llm(monkeypatch, tmp_path, on_chapter=on_chapter)
    n = _import(client)
    nid = n["id"]
    r = client.post(f"/api/novels/{nid}/analyze", json={})
    assert r.status_code == 200
    assert entered.wait(10), "分析线程未进入 LLM 调用"
    r2 = client.post(f"/api/novels/{nid}/analyze", json={})
    assert r2.status_code == 409
    release.set()
    st = _wait_analysis(client, nid)
    assert st["done"] == 2 and st["error"] == 0


def test_merge_failure_falls_back_to_local(client, fresh_db, monkeypatch, tmp_path):
    # LLM 合并建议失败 → 不阻塞，本地合并结果 + 自动总纲
    _patch_llm(monkeypatch, tmp_path,
               merge=Exception("LLM 合并服务不可用"))
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    _wait_analysis(client, nid)
    r = client.post(f"/api/novels/{nid}/bible/generate")
    assert r.status_code == 200, r.text
    bible = r.json()
    # 无 LLM 合并：少年/白衣少女保持独立条目（本地精确名合并）
    names = {c["name"] for c in bible["characters"]}
    assert names == {"少年", "白衣少女", "林川", "苏檸", "长老"}, names
    assert "本地合并结果" in bible["bible"]["summary"]


# =====================================================================
# 实体编辑 / 审核版本
# =====================================================================
def _analyzed_bible(client, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    _wait_analysis(client, nid)
    bible = client.post(f"/api/novels/{nid}/bible/generate").json()
    return nid, bible


def test_entity_crud(client, fresh_db, monkeypatch, tmp_path):
    nid, bible = _analyzed_bible(client, monkeypatch, tmp_path)
    lin = next(c for c in bible["characters"] if c["name"] == "林川")

    r = client.put(f"/api/novels/{nid}/bible/characters/{lin['id']}",
                   json={"catchphrase": "剑在心中"})
    assert r.status_code == 200 and r.json()["catchphrase"] == "剑在心中"

    r = client.post(f"/api/novels/{nid}/bible/characters",
                    json={"name": "神秘人", "role": "反派", "appearance": "斗篷遮面"})
    assert r.status_code == 201 and r.json()["name"] == "神秘人"
    new_id = r.json()["id"]

    r = client.post(f"/api/novels/{nid}/bible/timeline", json={"title": "暗流涌动"})
    assert r.status_code == 201 and r.json()["seq"] > 0  # 接在既有事件之后

    rel = bible["relations"][0]
    r = client.delete(f"/api/novels/{nid}/bible/relations/{rel['id']}")
    assert r.status_code == 204

    after = client.get(f"/api/novels/{nid}/bible").json()
    names = {c["name"] for c in after["characters"]}
    assert "神秘人" in names and "林川" in names
    assert next(c for c in after["characters"] if c["name"] == "林川")["catchphrase"] == "剑在心中"
    assert not after["relations"]
    assert after["timeline"][-1]["title"] == "暗流涌动"

    # 删不存在的实体 → 404
    r = client.delete(f"/api/novels/{nid}/bible/characters/{new_id}_nope")
    assert r.status_code == 404


def test_approve_creates_immutable_versions(client, fresh_db, monkeypatch, tmp_path):
    nid, bible = _analyzed_bible(client, monkeypatch, tmp_path)
    lin = next(c for c in bible["characters"] if c["name"] == "林川")
    client.put(f"/api/novels/{nid}/bible/characters/{lin['id']}",
               json={"catchphrase": "剑在心中"})

    r = client.post(f"/api/novels/{nid}/bible/approve", json={"note": "第一批审核"})
    assert r.status_code == 200 and r.json()["version"] == 1
    v1 = r.json()["snapshot"]
    lin_v1 = next(c for c in v1["characters"] if c["name"] == "林川")
    assert lin_v1["catchphrase"] == "剑在心中"

    cur = client.get(f"/api/novels/{nid}/bible").json()
    assert cur["bible"]["status"] == "approved" and cur["bible"]["version"] == 1
    assert [v["version"] for v in cur["versions"]] == [1]

    # 审核后再改动 → 回到草稿态（提示有未审核修改）
    client.put(f"/api/novels/{nid}/bible/characters/{lin['id']}",
               json={"catchphrase": "剑随心动"})
    cur = client.get(f"/api/novels/{nid}/bible").json()
    assert cur["bible"]["status"] == "draft"

    # 版本快照不可变：v1 仍是审核时的值
    r = client.get(f"/api/novels/{nid}/bible/versions/1")
    assert r.status_code == 200
    lin_snap = next(c for c in r.json()["snapshot"]["characters"] if c["name"] == "林川")
    assert lin_snap["catchphrase"] == "剑在心中"

    # 再次审核 → v2
    r = client.post(f"/api/novels/{nid}/bible/approve", json={"note": "二批"})
    assert r.json()["version"] == 2
    cur = client.get(f"/api/novels/{nid}/bible").json()
    assert [v["version"] for v in cur["versions"]] == [2, 1]

    # 重新生成替换实体，但版本历史保留
    r = client.post(f"/api/novels/{nid}/bible/generate")
    assert r.status_code == 200
    cur = r.json()
    assert [v["version"] for v in cur["versions"]] == [2, 1]
    assert cur["bible"]["status"] == "draft"


# =====================================================================
# 数据安全 / 兼容
# =====================================================================
def test_reparse_keeps_analyses_until_next_run(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    st = _wait_analysis(client, nid)
    old_ids = {it["chapter_id"] for it in st["items"]}
    assert len(old_ids) == 2

    # 重新识别：章节 id 更换，但既有分析行保留（不丢成果）
    chapters = client.post(f"/api/novels/{nid}/reparse").json()
    new_ids = {c["id"] for c in chapters}
    assert new_ids != old_ids
    st = client.get(f"/api/novels/{nid}/analyze").json()
    assert {it["chapter_id"] for it in st["items"]} == old_ids

    # 强制重跑：失效章节的旧行被清理，为新章节建行
    r = client.post(f"/api/novels/{nid}/analyze", json={"force": True})
    assert r.status_code == 200 and r.json()["started"] == 2
    st = _wait_analysis(client, nid)
    assert {it["chapter_id"] for it in st["items"]} == new_ids
    assert st["done"] == 2


def test_delete_novel_cascades_bible(client, fresh_db, monkeypatch, tmp_path):
    nid, _ = _analyzed_bible(client, monkeypatch, tmp_path)
    r = client.delete(f"/api/novels/{nid}")
    assert r.status_code == 204
    assert client.get(f"/api/novels/{nid}/bible").status_code == 404
    # 级联到底：bible 实体与分析行都不残留
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM bible_characters")["c"] == 0
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM novel_chapter_analyses")["c"] == 0
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM novel_bibles")["c"] == 0


def test_old_interfaces_unaffected(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    _wait_analysis(client, nid)
    client.post(f"/api/novels/{nid}/bible/generate")
    # 项目/镜头/质量档位等旧接口不受小说线影响
    assert client.get("/api/presets").status_code == 200
    r = client.post("/api/projects", json={"name": "旧接口兼容"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert client.get(f"/api/projects/{pid}").status_code == 200
    r = client.post(f"/api/projects/{pid}/scenes", json={"title": "场景一"})
    assert r.status_code == 201, r.text
