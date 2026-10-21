"""验收测试：一章小说 → 完整可编辑动漫分镜（PR-N01~N04 端到端，LLM 全 mock）。

对应验收要求：
- 先实现一章小说到完整可编辑动漫分镜的流程；
- 所有 AI 结果保留原文来源、支持人工修改和重新生成；
- 不得覆盖旧项目或已完成视频；
- 只建数据，不自动启动视频生成。

流程：导入单章小说（无章节标题 → 退化为「全文」单章）→ AI 章节分析 →
故事圣经 → 自动分集 → 动漫剧本 → 审核 → 结构化分镜 → 人工修改 →
重新生成 → 审核 → 交接项目 → 项目内分镜可继续编辑。
"""
import json
import re
import time

from app import llm as llm_mod
from app import settings as settings_mod

# 单章小说：没有任何章节标题（短篇/无标题文本）
NOVEL_TEXT = (
    "雨夜，少年林川沿着青石小路走进竹林。薄雾在脚下流淌，剑鸣自远处传来。\n"
    "竹林深处，白衣少女苏檸按剑而立，目光冷冷逼视：你终于来了。\n"
    "林川拔剑突进，苏檸横剑格挡，剑气纵横。灰袍长老现身拂袖喝止：住手！\n"
)

CH_RESP = {
    "summary": "林川雨夜入竹林，与苏檸对峙，被长老喝止",
    "characters": [
        {"name": "林川", "aliases": [], "role": "主角", "appearance": "青衫少年",
         "clothing": "青衫", "personality": "沉稳", "catchphrase": ""},
        {"name": "苏檸", "aliases": [], "role": "主角", "appearance": "白衣少女",
         "clothing": "白裙", "personality": "冷傲", "catchphrase": "你终于来了。"},
        {"name": "长老", "aliases": [], "role": "配角", "appearance": "灰袍老者",
         "clothing": "灰袍", "personality": "威严", "catchphrase": "住手！"},
    ],
    "scenes": [{"name": "雨夜竹林", "description": "薄雾青竹，雨夜",
                "atmosphere": "清冷杀机"}],
    "events": [{"title": "竹林对峙", "summary": "林川与苏檸对峙，长老喝止",
                "characters": ["林川", "苏檸", "长老"]}],
    "relations": [{"from": "林川", "to": "苏檸", "type": "敌对",
                   "description": "立场对立"}],
}

MERGE_RESP = {"merges": [], "summary": "林川与苏檸雨夜竹林对峙。"}

# 分集大纲：唯一章节标题是「全文」（无标题退化）
OUTLINE_RESP = {
    "episodes": [
        {"title": "全一话 雨夜竹林", "logline": "林川与苏檸雨夜竹林对峙",
         "chapter_titles": ["全文"], "duration_sec": 90,
         "beats": [{"title": "入林对峙", "summary": "两人竹林对峙，长老喝止"}]},
    ]
}

SCRIPT_TEXT = (
    "场景一：雨夜竹林\n"
    "林川沿青石小路走入竹林，薄雾流淌（远景, 推镜, 4秒）\n"
    "苏檸按剑而立：你终于来了。\n"
    "场景二：剑气纵横\n"
    "两人同时拔剑，剑气纵横（中景, 固定, 3秒）\n"
    "灰袍长老现身拂袖喝止（全景, 摇镜, 3秒）\n"
    "长老：住手！"
)

# 结构化分镜：首镜头故意给 continuation（应被强制 t2v）
PLAN_RESP = {
    "scenes": [
        {"title": "雨夜竹林", "shots": [
            {"title": "入林", "shot_size": "远景", "camera_movement": "推镜",
             "action": "林川沿青石小路走入竹林，薄雾在脚下流淌",
             "dialogue": "", "characters": ["林川"],
             "position": "从画面左侧入画", "emotion": "压抑",
             "duration_sec": 4, "mode": "continuation"},
            {"title": "对峙", "shot_size": "中景", "camera_movement": "固定",
             "action": "苏檸按剑而立，目光冷冷逼视", "dialogue": "你终于来了。",
             "characters": ["林川", "苏檸"], "position": "画面中心对峙",
             "emotion": "紧张", "duration_sec": 5, "mode": "i2v"},
        ]},
        {"title": "剑气纵横", "shots": [
            {"title": "喝止", "shot_size": "全景", "camera_movement": "摇镜",
             "action": "灰袍长老现身，拂袖喝止两人", "dialogue": "住手！",
             "characters": ["长老"], "position": "画面右侧入画",
             "emotion": "威严", "duration_sec": 3, "mode": "continuation"},
        ]},
    ]
}


def _patch_llm(monkeypatch, tmp_path, *, plan=None):
    """mock LLM：按 system prompt 分发（章节分析/合并/分集/剧本/分镜）。"""
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps(
        {"llm": {"base_url": "http://llm.test", "model": "test-model"}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", cfg)
    calls = {"plan": 0}

    def fake_chat(messages, **kwargs):
        system = messages[0]["content"]
        if "设定整理专家" in system:
            return json.dumps(MERGE_RESP, ensure_ascii=False)
        if "分集规划" in system:
            return json.dumps(OUTLINE_RESP, ensure_ascii=False)
        if "动画编剧" in system:
            return SCRIPT_TEXT
        if "分镜师" in system:
            calls["plan"] += 1
            if plan is None:
                return json.dumps(PLAN_RESP, ensure_ascii=False)
            return plan if isinstance(plan, str) else json.dumps(
                plan, ensure_ascii=False)
        # 章节分析（单章小说只有一个标题「全文」）
        return json.dumps(CH_RESP, ensure_ascii=False)

    monkeypatch.setattr(llm_mod, "chat", fake_chat)
    monkeypatch.setattr(llm_mod, "is_configured", lambda: True)
    return calls


def _wait_analysis(client, nid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/novels/{nid}/analyze").json()
        if not st["running"] and st["pending"] == 0:
            return st
        time.sleep(0.05)
    raise AssertionError("分析未在超时时间内完成")


def test_single_chapter_novel_to_editable_storyboard(client, fresh_db,
                                                     monkeypatch, tmp_path):
    """一章小说 → 完整可编辑动漫分镜（验收主链路）。"""
    from app.db import db

    # mock LLM（全链路不触网）
    _patch_llm(monkeypatch, tmp_path)

    # ---------- 预置旧项目（验证不被覆盖） ----------
    old = client.post("/api/projects", json={"name": "旧项目"}).json()
    old_scene = client.post(f"/api/projects/{old['id']}/scenes",
                            json={"title": "旧场景"}).json()

    # ---------- PR-N01：导入单章小说 ----------
    r = client.post("/api/novels",
                    files={"file": ("duanpian.txt", NOVEL_TEXT.encode("utf-8"),
                                    "text/plain")},
                    data={"title": "雨夜竹林"})
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    chs = client.get(f"/api/novels/{nid}/chapters").json()
    assert len(chs) == 1  # 无标题文本退化为单章
    assert chs[0]["title"] == "全文"
    assert chs[0]["start"] == 0 and chs[0]["end"] == len(NOVEL_TEXT)
    assert r.json()["content_sha256"]

    # ---------- PR-N02：章节分析 + 故事圣经 ----------
    r = client.post(f"/api/novels/{nid}/analyze", json={})
    assert r.status_code == 200, r.text
    st = _wait_analysis(client, nid)
    assert st["done"] == st["total"] == 1 and st["error"] == 0, st
    assert client.post(f"/api/novels/{nid}/bible/generate").status_code == 200
    bible = client.get(f"/api/novels/{nid}/bible").json()
    names = {c["name"] for c in bible["characters"]}
    assert {"林川", "苏檸", "长老"} <= names

    # ---------- PR-N03：自动分集 + 动漫剧本 + 审核 ----------
    eps = client.post(f"/api/novels/{nid}/episodes/generate", json={}).json()
    assert len(eps) == 1
    eid = eps[0]["id"]
    # 分集章节快照可定位原小说
    refs = eps[0]["chapter_refs"]
    assert refs and refs[0]["title"] == "全文"
    assert NOVEL_TEXT[refs[0]["start"]:refs[0]["end"]].strip()  # 偏移有效
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    assert r.status_code == 200, r.text
    script = r.json()
    assert script["scene_count"] >= 1 and script["shot_count"] >= 1
    assert client.post(
        f"/api/novels/{nid}/episodes/{eid}/script/approve",
        json={}).status_code == 200

    # ---------- PR-N04：结构化分镜 ----------
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 200, r.text
    plan = r.json()
    items = plan["items"]
    assert len(items) == 3 and plan["shot_count"] == 3
    # 首镜头强制 t2v；其余按 LLM 草案
    assert [i["mode"] for i in items] == ["t2v", "i2v", "continuation"]
    # 每个镜头都带章节快照（定位原小说）
    for it in items:
        assert it["chapter_refs"] and it["chapter_refs"][0]["title"] == "全文"
    assert items[1]["dialogue"] == "你终于来了。"

    # ---------- 人工修改 ----------
    iid = items[0]["id"]
    r = client.put(f"/api/novels/{nid}/episodes/{eid}/shots/{iid}",
                   json={"title": "入林（人工修订）", "duration_sec": 6})
    assert r.status_code == 200 and r.json()["title"] == "入林（人工修订）"
    st = client.get(f"/api/novels/{nid}/episodes/{eid}/shots").json()
    assert st["total_duration_sec"] == 14  # 6 + 5 + 3

    # ---------- 重新生成（AI 草案可重跑，不锁死人工修改前的状态） ----------
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 200
    plan2 = r.json()
    assert plan2["version"] == 0 and plan2["status"] == "draft"
    assert [i["title"] for i in plan2["items"]] == ["入林", "对峙", "喝止"]

    # ---------- 审核 + 交接项目 ----------
    assert client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve",
                       json={"note": "验收审核"}).status_code == 200
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    assert r.json()["shot_count"] == 3

    from app import assets as assets_mod
    from app import storyboard as sb
    scenes = sb.list_scenes(pid)
    assert [s["title"] for s in scenes] == ["雨夜竹林", "剑气纵横"]
    shots = [s for sc in scenes for s in sc["shots"]]
    assert len(shots) == 3
    assert [s["shot_size"] for s in shots] == ["远景", "中景", "全景"]
    assert [s["mode"] for s in shots] == ["t2v", "i2v", "continuation"]
    assert shots[1]["dialogue"] == "你终于来了。"
    # prompt 取材故事圣经（场景描述 + 人物外貌）
    assert "薄雾青竹" in shots[0]["prompt"]
    assert "青衫少年" in shots[0]["prompt"]
    # 角色卡自动建档 + character_ids 关联
    chars = {c["name"]: c for c in assets_mod.list_characters(pid)}
    assert set(chars) == {"林川", "苏檸", "长老"}
    assert set(shots[1]["character_ids"]) == {chars["林川"]["id"],
                                              chars["苏檸"]["id"]}

    # ---------- 不自动启动生成 ----------
    assert db.fetch_one("SELECT COUNT(*) AS c FROM jobs")["c"] == 0
    assert all(not s.get("job_id") for s in shots)

    # ---------- 项目内分镜可继续编辑 ----------
    r = client.patch(f"/api/shots/{shots[0]['id']}",
                     json={"title": "入林（工作台再修订）"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "入林（工作台再修订）"

    # ---------- 旧项目/旧接口不受影响 ----------
    old_scenes = client.get(f"/api/projects/{old['id']}/scenes").json()
    assert [s["id"] for s in old_scenes] == [old_scene["id"]]
    assert old["id"] != pid
    projects = client.get("/api/projects").json()
    assert any(p["id"] == old["id"] for p in projects)

    # ---------- 原文来源可定位（ Bible 快照 → 章节偏移 → 原文切片） ----------
    ref = items[0]["chapter_refs"][0]
    novel = __import__("app.novel", fromlist=["get_novel"]).get_novel(
        nid, with_content=True)
    assert novel["content"][ref["start"]:ref["end"]] == \
        NOVEL_TEXT[ref["start"]:ref["end"]]
    assert "林川" in novel["content"][ref["start"]:ref["end"]]
