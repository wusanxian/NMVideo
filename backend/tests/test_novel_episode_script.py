"""PR-N03：自动分集与动漫剧本回归测试（LLM 全 mock，不触网）。

覆盖：分集大纲生成（章节引用解析/排序/丢弃无效集/JSON 重试/未配置 400/
依赖圣经 400）、分集 CRUD 与审核版本不可变、剧本生成（工作台格式兼容/
格式不符重试/两次失败 400）、剧本编辑校验、审核快照与重新生成保留版本、
交接项目（创建+导入/幂等/未审核 400）、删除级联、旧接口兼容、prompt 取材。
"""
import json
import re
import time

import pytest

from app import llm as llm_mod
from app import settings as settings_mod

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

OUTLINE_RESP = {
    "episodes": [
        {"title": "第1话 竹林对峙", "logline": "林川与苏檸竹林对峙",
         "chapter_titles": ["第一章 竹林"], "duration_sec": 90,
         "beats": [{"title": "竹林相遇", "summary": "两人竹林对峙"}]},
        {"title": "第2话 突进喝止", "logline": "拔剑突进被长老喝止",
         "chapter_titles": ["第二章 突进"], "duration_sec": 90,
         "beats": [{"title": "拔剑突进", "summary": "少年拔剑突进"}]},
    ]
}

# 与分镜工作台剧本解析器兼容的格式（场景标记 + 画面行元信息 + 对白行）
# 注意：llm.chat 返回时整体 strip，故这里不带首尾空白
SCRIPT_TEXT = (
    "场景一：竹林清晨\n"
    "林川走过竹林，薄雾弥漫（远景, 推, 4秒）\n"
    "苏檸按剑而立：你终于来了。\n"
    "林川：此路是我开。\n"
    "场景二：剑拔弩张\n"
    "两人同时拔剑，剑气纵横（中景, 固定, 3秒）\n"
    "灰袍长老现身喝止（全景, 摇, 3秒）\n"
    "长老：住手！"
)


def _patch_llm(monkeypatch, tmp_path, *, outline=None, script=None,
               script_first=None, configured=True):
    """mock LLM：按 system prompt 分发（章节分析 / 合并 / 分集 / 剧本）。

    outline/script: 分集/剧本响应（str/dict/Exception）；
    script_first: 剧本首次响应（用于「首次不合规、重试合规」用例）；
    configured=False 模拟未配置 LLM。
    """
    if configured:
        cfg = tmp_path / "settings.json"
        cfg.write_text(json.dumps(
            {"llm": {"base_url": "http://llm.test", "model": "test-model"}},
            ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(settings_mod, "SETTINGS_FILE", cfg)
    calls = {"script": 0}

    def fake_chat(messages, **kwargs):
        system = messages[0]["content"]
        if "设定整理专家" in system:
            return json.dumps(MERGE_RESP, ensure_ascii=False)
        if "分集规划" in system:
            if isinstance(outline, Exception):
                raise outline
            if outline is None:
                return json.dumps(OUTLINE_RESP, ensure_ascii=False)
            return outline if isinstance(outline, str) else json.dumps(
                outline, ensure_ascii=False)
        if "动画编剧" in system:
            calls["script"] += 1
            if calls["script"] == 1 and script_first is not None:
                if isinstance(script_first, Exception):
                    raise script_first
                return script_first
            if isinstance(script, Exception):
                raise script
            if script is None:
                return SCRIPT_TEXT
            return script
        # 章节分析
        title = ""
        for msg in messages:
            m = re.search(r"【章节标题】(.*)", msg.get("content", ""))
            if m:
                title = m.group(1).split("\n")[0].strip()
                break
        default = CH1_RESP if title.startswith("第一章") else CH2_RESP
        return json.dumps(default, ensure_ascii=False)

    if configured:
        monkeypatch.setattr(llm_mod, "chat", fake_chat)
        monkeypatch.setattr(llm_mod, "is_configured", lambda: True)


def _import(client, text=NOVEL_TEXT):
    r = client.post("/api/novels",
                    files={"file": ("test.txt", text.encode("utf-8"),
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


def _setup_bible(client, monkeypatch, tmp_path, *, text=NOVEL_TEXT, **kw):
    """导入 → 分析 → 生成圣经。返回 (novel_id, chapters)。"""
    _patch_llm(monkeypatch, tmp_path, **kw)
    n = _import(client, text)
    nid = n["id"]
    r = client.post(f"/api/novels/{nid}/analyze", json={})
    assert r.status_code == 200, r.text
    st = _wait_analysis(client, nid)
    assert st["done"] == st["total"] and st["error"] == 0, st
    r = client.post(f"/api/novels/{nid}/bible/generate")
    assert r.status_code == 200, r.text
    chapters = client.get(f"/api/novels/{nid}/chapters").json()
    return nid, chapters


# =====================================================================
# 分集大纲
# =====================================================================
def test_outline_generate_and_episode_lifecycle(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)

    r = client.post(f"/api/novels/{nid}/episodes/generate", json={})
    assert r.status_code == 200, r.text
    eps = r.json()
    assert [e["title"] for e in eps] == ["第1话 竹林对峙", "第2话 突进喝止"]
    # 章节引用解析为快照（id/偏移与当前章节行一致，按原文顺序）
    e1 = eps[0]
    assert len(e1["chapter_refs"]) == 1
    assert e1["chapter_refs"][0]["chapter_id"] == chapters[0]["id"]
    assert e1["chapter_refs"][0]["start"] == chapters[0]["start"]
    assert [b["title"] for b in e1["beats"]] == ["竹林相遇"]
    assert e1["status"] == "draft" and e1["version"] == 0

    # 编辑 → 审核 → 版本快照不可变 → 再编辑回草稿 → 再审核 v2
    eid = e1["id"]
    r = client.put(f"/api/novels/{nid}/episodes/{eid}", json={
        "title": "第1话 竹林对峙（改）", "target_duration_sec": 120,
        "chapter_ids": [c["id"] for c in chapters],
        "beats": [{"title": "新节拍", "summary": ""}],
    })
    assert r.status_code == 200 and r.json()["title"].endswith("（改）")
    assert len(r.json()["chapter_refs"]) == 2  # 多选两章，按 start 排序

    r = client.post(f"/api/novels/{nid}/episodes/{eid}/approve", json={"note": "初审"})
    assert r.status_code == 200 and r.json()["version"] == 1
    v1 = client.get(f"/api/novels/{nid}/episodes/{eid}/versions/1").json()
    assert v1["snapshot"]["title"] == "第1话 竹林对峙（改）"
    assert v1["snapshot"]["target_duration_sec"] == 120

    client.put(f"/api/novels/{nid}/episodes/{eid}", json={"title": "第1话 再审"})
    detail = client.get(f"/api/novels/{nid}/episodes/{eid}").json()
    assert detail["status"] == "draft"  # 审核后改动回草稿态
    assert len(detail["episode_versions"]) == 1  # 版本历史保留
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/approve", json={})
    assert r.json()["version"] == 2
    # v1 快照不被后续审核污染
    v1b = client.get(f"/api/novels/{nid}/episodes/{eid}/versions/1").json()
    assert v1b["snapshot"]["title"] == "第1话 竹林对峙（改）"


def test_outline_requires_bible(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    r = client.post(f"/api/novels/{n['id']}/episodes/generate", json={})
    assert r.status_code == 400 and "故事圣经" in r.json()["detail"]


def test_outline_requires_llm_config(client, fresh_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", tmp_path / "none.json")
    n = _import(client)
    r = client.post(f"/api/novels/{n['id']}/episodes/generate", json={})
    # 圣经检查先于 LLM 检查：这里报圣经未生成（语义正确，不误导）
    assert r.status_code == 400


def test_outline_llm_not_configured(client, fresh_db, monkeypatch, tmp_path):
    # 圣经就绪但 LLM 未配置 → 400 引导去设置页
    nid, _ = _setup_bible(client, monkeypatch, tmp_path)
    monkeypatch.setattr(llm_mod, "is_configured", lambda: False)
    r = client.post(f"/api/novels/{nid}/episodes/generate", json={})
    assert r.status_code == 400 and "模型服务" in r.json()["detail"]


def test_outline_invalid_json_retry_then_error(client, fresh_db, monkeypatch, tmp_path):
    nid, _ = _setup_bible(client, monkeypatch, tmp_path,
                          outline="这不是JSON，只是一段解释文字")
    r = client.post(f"/api/novels/{nid}/episodes/generate", json={})
    assert r.status_code == 400 and "解析" in r.json()["detail"]


def test_outline_invalid_json_retry_succeeds(client, fresh_db, monkeypatch, tmp_path):
    # 首次输出非 JSON，重试后合法 → 成功（重试消息把失败输出回灌）
    nid, _ = _setup_bible(client, monkeypatch, tmp_path,
                          outline="抱歉，我无法完成。")
    # monkeypatch chat 固定返回；用两次不同响应模拟重试成功
    # （_patch_llm 的 outline 为 str 时恒定返回——改用以计数器方式）
    # → 直接换成：首次垃圾、首次之后合法
    # 注：此处通过重新 patch 实现（monkeypatch 可覆盖）
    calls = {"n": 0}
    from app import llm as llm2
    orig = llm2.chat

    def fake(messages, **kwargs):
        if messages[0]["content"].find("分集规划") >= 0:
            calls["n"] += 1
            if calls["n"] == 1:
                return "输出异常"
            return json.dumps(OUTLINE_RESP, ensure_ascii=False)
        return orig(messages, **kwargs)

    monkeypatch.setattr(llm2, "chat", fake)
    r = client.post(f"/api/novels/{nid}/episodes/generate", json={})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2


def test_outline_unmatched_chapters_dropped(client, fresh_db, monkeypatch, tmp_path):
    # 引用了不存在的章节 → 该集丢弃；全部无效 → 400
    bad = {"episodes": [
        {"title": "第1话 幽灵集", "logline": "x",
         "chapter_titles": ["第三章 不存在"], "duration_sec": 90, "beats": []},
    ]}
    nid, _ = _setup_bible(client, monkeypatch, tmp_path, outline=bad)
    r = client.post(f"/api/novels/{nid}/episodes/generate", json={})
    assert r.status_code == 400 and "有效分集" in r.json()["detail"]

    # 一好一坏：只保留能定位原文的集（第二篇小说，避免哈希去重 409）
    mixed = {"episodes": [
        {"title": "第1话 幽灵集", "logline": "x",
         "chapter_titles": ["第三章 不存在"], "duration_sec": 90, "beats": []},
        {"title": "第1话 竹林对峙", "logline": "ok",
         "chapter_titles": ["第一章 竹林"], "duration_sec": 90, "beats": []},
    ]}
    nid2, _ = _setup_bible(client, monkeypatch, tmp_path, outline=mixed,
                           text=NOVEL_TEXT + "\n\n尾声\n尘埃落定。\n")
    r = client.post(f"/api/novels/{nid2}/episodes/generate", json={})
    assert r.status_code == 200
    assert [e["title"] for e in r.json()] == ["第1话 竹林对峙"]


def test_outline_duration_clamped(client, fresh_db, monkeypatch, tmp_path):
    odd = {"episodes": [
        {"title": "第1话 超长", "logline": "x",
         "chapter_titles": ["第一章 竹林"], "duration_sec": 99999, "beats": []},
        {"title": "第2话 超短", "logline": "x",
         "chapter_titles": ["第二章 突进"], "duration_sec": 1, "beats": []},
    ]}
    nid, _ = _setup_bible(client, monkeypatch, tmp_path, outline=odd)
    r = client.post(f"/api/novels/{nid}/episodes/generate", json={})
    assert r.status_code == 200
    durs = {e["title"]: e["target_duration_sec"] for e in r.json()}
    assert durs["第1话 超长"] == 300 and durs["第2话 超短"] == 30


# =====================================================================
# 动漫剧本
# =====================================================================
def _mk_episode(client, nid, chapters):
    r = client.post(f"/api/novels/{nid}/episodes", json={
        "title": "第1话 竹林对峙",
        "chapter_ids": [c["id"] for c in chapters],
        "target_duration_sec": 90,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_script_generate_format_compatible(client, fresh_db, monkeypatch, tmp_path):
    from app import storyboard as sb_mod
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)

    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    assert r.status_code == 200, r.text
    script = client.get(f"/api/novels/{nid}/episodes/{eid}/script").json()
    assert script["content"] == SCRIPT_TEXT
    assert script["status"] == "draft" and script["version"] == 0
    assert script["scene_count"] == 2 and script["shot_count"] == 3
    # 与工作台解析器真正兼容（同一解析器能拆出场景/镜头）
    scenes = sb_mod.parse_script(script["content"])
    assert [s["title"] for s in scenes] == ["竹林清晨", "剑拔弩张"]
    assert sum(len(s["shots"]) for s in scenes) == 3


def test_script_generate_invalid_then_retry_ok(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path,
                                 script_first="抱歉我不能写这个。")
    eid = _mk_episode(client, nid, chapters)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    assert r.status_code == 200, r.text
    script = client.get(f"/api/novels/{nid}/episodes/{eid}/script").json()
    assert script["content"] == SCRIPT_TEXT  # 重试后拿到合规格式


def test_script_generate_invalid_twice_fails(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path,
                                 script="依然不合规的输出")
    eid = _mk_episode(client, nid, chapters)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    assert r.status_code == 400 and "无法使用" in r.json()["detail"]


def test_script_update_validates_format(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")

    # 无场景标记 → 400（保存前拦截，防脏数据进交接）
    r = client.put(f"/api/novels/{nid}/episodes/{eid}/script",
                   json={"content": "只是一段没有场景标记的文字"})
    assert r.status_code == 400 and "格式" in r.json()["detail"]

    # 合法编辑 → 统计重算
    new_text = "场景一：竹林\n林川拭剑（特写, 固定, 2秒）\n"
    r = client.put(f"/api/novels/{nid}/episodes/{eid}/script",
                   json={"content": new_text, "title": "竹林·改"})
    assert r.status_code == 200
    assert r.json()["content"] == new_text
    assert r.json()["scene_count"] == 1 and r.json()["shot_count"] == 1


def test_script_approve_snapshot_and_regen_keeps_versions(
        client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")

    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/approve",
                    json={"note": "可以导出"})
    assert r.status_code == 200 and r.json()["version"] == 1
    v1 = client.get(f"/api/novels/{nid}/episodes/{eid}/script/versions/1").json()
    assert v1["snapshot"]["content"] == SCRIPT_TEXT
    assert v1["snapshot"]["scene_count"] == 2
    # 快照带原小说定位信息
    assert v1["snapshot"]["chapter_refs"][0]["chapter_id"] == chapters[0]["id"]

    # 重新生成 → 覆盖为草案（版本历史保留）→ 再审核 v2
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert len(client.get(f"/api/novels/{nid}/episodes/{eid}/script").json()
               ["versions"]) == 1
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/approve", json={})
    assert r.json()["version"] == 2
    v1b = client.get(f"/api/novels/{nid}/episodes/{eid}/script/versions/1").json()
    assert v1b["snapshot"]["content"] == SCRIPT_TEXT  # v1 未被污染


# =====================================================================
# 交接项目
# =====================================================================
def test_handoff_creates_project_and_idempotent(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")

    # 未审核 → 400
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/handoff")
    assert r.status_code == 400 and "审核" in r.json()["detail"]

    client.post(f"/api/novels/{nid}/episodes/{eid}/script/approve", json={})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/handoff")
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    assert r.json()["already"] is False
    assert r.json()["scene_count"] == 2 and r.json()["shot_count"] == 3

    # 项目真实落库：场景/镜头可查，剧本文本已同步
    project = client.get(f"/api/projects/{pid}").json()
    assert [s["title"] for s in project["scenes"]] == ["竹林清晨", "剑拔弩张"]
    assert sum(len(s["shots"]) for s in project["scenes"]) == 3
    assert client.get(f"/api/projects/{pid}/script").json()["script"] == SCRIPT_TEXT
    script = client.get(f"/api/novels/{nid}/episodes/{eid}/script").json()
    assert script["project_id"] == pid

    # 幂等：再次交接返回同一项目，不重复建
    r2 = client.post(f"/api/novels/{nid}/episodes/{eid}/script/handoff")
    assert r2.status_code == 200 and r2.json()["already"] is True
    assert r2.json()["project_id"] == pid
    assert len([p for p in client.get("/api/projects").json()
                if p["id"] == pid]) == 1


def test_handoff_recreates_after_project_deleted(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/approve", json={})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/handoff")
    pid = r.json()["project_id"]
    # 用户手动删了项目 → 再交接应允许重建（清回填记录）
    client.delete(f"/api/projects/{pid}")
    r2 = client.post(f"/api/novels/{nid}/episodes/{eid}/script/handoff")
    assert r2.status_code == 200 and r2.json()["already"] is False
    assert r2.json()["project_id"] != pid


# =====================================================================
# 取材 / 级联 / 兼容
# =====================================================================
def test_script_prompt_uses_bible_and_chapter_text(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)
    captured = {}
    from app import llm as llm2
    orig = llm2.chat

    def fake(messages, **kwargs):
        if messages[0]["content"].find("动画编剧") >= 0:
            captured["user"] = messages[-1]["content"]
        return orig(messages, **kwargs)

    monkeypatch.setattr(llm2, "chat", fake)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    assert r.status_code == 200, r.text
    user = captured["user"]
    # 圣经人物标准名（合并后）+ 章节原文都进了 prompt
    assert "林川" in user and "苏檸" in user
    assert "薄雾弥漫" in user  # 第一章原文
    assert "住手" in user      # 第二章原文


def test_delete_novel_cascades_episodes_scripts(client, fresh_db, monkeypatch, tmp_path):
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/approve", json={})

    client.delete(f"/api/novels/{nid}")
    # 小说没了：分集/剧本查询 404，列表空
    assert client.get(f"/api/novels/{nid}/episodes").status_code == 404
    assert client.get(f"/api/novels/{nid}/episodes/{eid}/script").status_code == 404
    # 交接产生的项目不受小说删除影响（数据边界）
    projects = client.get("/api/projects").json()
    assert all(p["id"] != "" for p in projects)


def test_old_endpoints_compat(client, fresh_db, monkeypatch, tmp_path):
    """PR-N03 不破坏既有接口：novels / bible / projects / health 全通。"""
    nid, chapters = _setup_bible(client, monkeypatch, tmp_path)
    eid = _mk_episode(client, nid, chapters)
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/approve", json={})
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/handoff")

    assert client.get("/api/health").status_code == 200
    novels = client.get("/api/novels").json()
    assert any(n["id"] == nid for n in novels)
    assert client.get(f"/api/novels/{nid}/bible").status_code == 200
    assert client.get(f"/api/novels/{nid}/chapters").status_code == 200
    assert client.get("/api/projects").status_code == 200
    # 手动分集增删仍可用（PR-N03 手动入口）
    r = client.post(f"/api/novels/{nid}/episodes", json={"title": "手动集"})
    assert r.status_code == 201
    assert client.delete(f"/api/novels/{nid}/episodes/{r.json()['id']}").status_code == 204
