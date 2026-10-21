"""PR-N04：结构化分镜回归测试（LLM 全 mock，不触网）。

覆盖：分镜生成（依赖审核态剧本 400 / 未配置 400 / JSON 重试 / 非法值归一 /
首镜头强制 t2v / 章节快照定位）、镜头编辑白名单与时长钳制、重排、审核版本
不可变、交接项目（场景/分镜/角色卡建档/模式与对白落位 / 幂等 / 未审核 400 /
已有成果拒绝）、删除级联、旧接口兼容、prompt 取材圣经。
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
        {"name": "林川", "aliases": [], "role": "主角", "appearance": "青衫",
         "clothing": "青衫", "personality": "沉稳", "catchphrase": ""},
        {"name": "苏檸", "aliases": [], "role": "主角", "appearance": "白衣",
         "clothing": "白裙", "personality": "冷傲", "catchphrase": ""},
    ],
    "scenes": [{"name": "竹林", "description": "薄雾青竹", "atmosphere": "清冷"}],
    "events": [{"title": "竹林对峙", "summary": "两人竹林相遇对峙",
                "characters": ["林川", "苏檸"]}],
    "relations": [],
}

CH2_RESP = {
    "summary": "少年拔剑突进被长老喝止",
    "characters": [
        {"name": "林川", "aliases": [], "role": "主角", "appearance": "青衫",
         "clothing": "青衫", "personality": "沉稳", "catchphrase": ""},
        {"name": "苏檸", "aliases": [], "role": "主角", "appearance": "白衣",
         "clothing": "白裙", "personality": "冷傲", "catchphrase": ""},
        {"name": "长老", "aliases": [], "role": "配角", "appearance": "灰袍老者",
         "clothing": "灰袍", "personality": "威严", "catchphrase": "住手！"},
    ],
    "scenes": [{"name": "竹林", "description": "薄雾青竹，剑气纵横",
                "atmosphere": "清冷"}],
    "events": [], "relations": [],
}

MERGE_RESP = {"merges": [], "summary": "林川与苏檸竹林对峙。"}

OUTLINE_RESP = {
    "episodes": [
        {"title": "第1话 竹林对峙", "logline": "林川与苏檸竹林对峙",
         "chapter_titles": ["第一章 竹林"], "duration_sec": 90,
         "beats": [{"title": "竹林相遇", "summary": "两人竹林对峙"}]},
    ]
}

SCRIPT_TEXT = (
    "场景一：竹林清晨\n"
    "林川走过竹林，薄雾弥漫（远景, 推镜, 4秒）\n"
    "苏檸按剑而立：你终于来了。\n"
    "场景二：剑拔弩张\n"
    "两人同时拔剑，剑气纵横（中景, 固定, 3秒）\n"
    "长老：住手！"
)

# 合法分镜（首镜头 mode 故意给 continuation——应被强制为 t2v）
PLAN_RESP = {
    "scenes": [
        {"title": "竹林清晨", "shots": [
            {"title": "入林", "shot_size": "远景", "camera_movement": "推镜",
             "action": "林川沿小径走入竹林，薄雾弥漫", "dialogue": "",
             "characters": ["林川"], "position": "从画面左侧入画",
             "emotion": "压抑", "duration_sec": 4, "mode": "continuation"},
            {"title": "对峙", "shot_size": "中景", "camera_movement": "固定",
             "action": "苏檸按剑而立，目光冷冷逼视", "dialogue": "你终于来了。",
             "characters": ["林川", "苏檸"], "position": "画面中心对峙",
             "emotion": "紧张", "duration_sec": 5, "mode": "i2v"},
        ]},
        {"title": "剑拔弩张", "shots": [
            {"title": "喝止", "shot_size": "全景", "camera_movement": "摇镜",
             "action": "灰袍长老现身，拂袖喝止两人", "dialogue": "住手！",
             "characters": ["长老"], "position": "画面右侧入画",
             "emotion": "威严", "duration_sec": 3, "mode": "continuation"},
        ]},
    ]
}

# 非法值集中营：景别/机位/模式越界 + 时长越界 + 裸运动字
PLAN_DIRTY = {
    "scenes": [{"title": "竹林", "shots": [
        {"title": "镜头一", "shot_size": "超特写", "camera_movement": "翻滚",
         "action": "测试动作", "dialogue": "", "characters": ["不存在的人"],
         "position": "", "emotion": "", "duration_sec": 99, "mode": "魔法"},
        {"title": "镜头二", "shot_size": "中景", "camera_movement": "推",
         "action": "第二个动作", "dialogue": "", "characters": [],
         "position": "", "emotion": "", "duration_sec": 0.1, "mode": "t2v"},
    ]}]
}


def _patch_llm(monkeypatch, tmp_path, *, plan=None, configured=True):
    """mock LLM：按 system prompt 分发（章节分析 / 合并 / 分集 / 剧本 / 分镜）。

    plan: 分镜响应（str/dict/Exception）；None → PLAN_RESP。
    """
    if configured:
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
            # 重试场景：首次垃圾、之后合法（由 _plan_sequence 控制）
            seq = getattr(fake_chat, "_plan_seq", None)
            if seq and calls["plan"] <= len(seq):
                resp = seq[calls["plan"] - 1]
                if isinstance(resp, Exception):
                    raise resp
                return resp
            if isinstance(plan, Exception):
                raise plan
            if plan is None:
                return json.dumps(PLAN_RESP, ensure_ascii=False)
            return plan if isinstance(plan, str) else json.dumps(
                plan, ensure_ascii=False)
        # 章节分析
        title = ""
        for msg in messages:
            m = re.search(r"【章节标题】(.*)", msg.get("content", ""))
            if m:
                title = m.group(1).split("\n")[0].strip()
                break
        return json.dumps(CH1_RESP if title.startswith("第一章") else CH2_RESP,
                          ensure_ascii=False)

    if configured:
        monkeypatch.setattr(llm_mod, "chat", fake_chat)
        monkeypatch.setattr(llm_mod, "is_configured", lambda: True)
    return calls


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


def _setup_approved_script(client, monkeypatch, tmp_path, *, text=NOVEL_TEXT, **kw):
    """导入 → 分析 → 圣经 → 分集 → 剧本 → 审核剧本。返回 (novel_id, episode_id)。"""
    _patch_llm(monkeypatch, tmp_path, **kw)
    n = _import(client, text)
    nid = n["id"]
    r = client.post(f"/api/novels/{nid}/analyze", json={})
    assert r.status_code == 200, r.text
    st = _wait_analysis(client, nid)
    assert st["done"] == st["total"] and st["error"] == 0, st
    assert client.post(f"/api/novels/{nid}/bible/generate").status_code == 200
    eps = client.post(f"/api/novels/{nid}/episodes/generate", json={}).json()
    eid = eps[0]["id"]
    assert client.post(
        f"/api/novels/{nid}/episodes/{eid}/script/generate").status_code == 200
    assert client.post(
        f"/api/novels/{nid}/episodes/{eid}/script/approve",
        json={}).status_code == 200
    return nid, eid


# =====================================================================
# 生成与校验
# =====================================================================
def test_generate_plan_and_lifecycle(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)

    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["status"] == "draft" and plan["version"] == 0
    assert plan["shot_count"] == 3 and plan["total_duration_sec"] == 12
    items = plan["items"]
    assert [i["seq"] for i in items] == [1, 2, 3]
    # 首镜头强制 t2v（LLM 给了 continuation）
    assert items[0]["mode"] == "t2v"
    assert [i["mode"] for i in items[1:]] == ["i2v", "continuation"]
    assert items[0]["shot_size"] == "远景" and items[0]["camera_movement"] == "推镜"
    assert items[1]["character_names"] == ["林川", "苏檸"]
    assert items[1]["dialogue"] == "你终于来了。"
    # 章节快照定位原小说
    assert len(items[0]["chapter_refs"]) == 1
    assert items[0]["chapter_refs"][0]["title"] == "第一章 竹林"

    # 编辑 → 回草稿 → 审核 v1 → 再编辑 → 再审核 v2，v1 不被污染
    iid = items[0]["id"]
    r = client.put(f"/api/novels/{nid}/episodes/{eid}/shots/{iid}",
                   json={"title": "入林（改）", "duration_sec": 6,
                         "character_names": ["林川", "苏檸"]})
    assert r.status_code == 200 and r.json()["title"] == "入林（改）"
    assert r.json()["duration_sec"] == 6
    st = client.get(f"/api/novels/{nid}/episodes/{eid}/shots").json()
    assert st["total_duration_sec"] == 14  # 6 + 5 + 3
    assert st["status"] == "draft"

    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve",
                    json={"note": "初审"})
    assert r.status_code == 200 and r.json()["version"] == 1
    v1 = client.get(f"/api/novels/{nid}/episodes/{eid}/shots/versions/1").json()
    assert v1["note"] == "初审" and len(v1["snapshot"]["items"]) == 3
    assert v1["snapshot"]["items"][0]["title"] == "入林（改）"

    client.put(f"/api/novels/{nid}/episodes/{eid}/shots/{iid}",
               json={"title": "入林（再审）"})
    st = client.get(f"/api/novels/{nid}/episodes/{eid}/shots").json()
    assert st["status"] == "draft" and len(st["versions"]) == 1
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve", json={})
    assert r.json()["version"] == 2
    v1b = client.get(f"/api/novels/{nid}/episodes/{eid}/shots/versions/1").json()
    assert v1b["snapshot"]["items"][0]["title"] == "入林（改）"  # 快照不可变


def test_generate_requires_approved_script(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    _wait_analysis(client, nid)
    client.post(f"/api/novels/{nid}/bible/generate")
    eps = client.post(f"/api/novels/{nid}/episodes/generate", json={}).json()
    eid = eps[0]["id"]
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    # 剧本存在但未审核 → 400（工序次序：剧本审核 → 分镜）
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 400 and "审核" in r.json()["detail"]


def test_generate_requires_script(client, fresh_db, monkeypatch, tmp_path):
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    _wait_analysis(client, nid)
    client.post(f"/api/novels/{nid}/bible/generate")
    eps = client.post(f"/api/novels/{nid}/episodes/generate", json={}).json()
    r = client.post(f"/api/novels/{nid}/episodes/{eps[0]['id']}/shots/generate")
    assert r.status_code == 400 and "剧本" in r.json()["detail"]


def test_generate_llm_not_configured(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    monkeypatch.setattr(llm_mod, "is_configured", lambda: False)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 400 and "模型服务" in r.json()["detail"]


def test_generate_invalid_json_then_error(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path,
                                      plan="这不是JSON，只是解释文字")
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 400 and "解析" in r.json()["detail"]


def test_generate_invalid_json_retry_succeeds(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    # 首次垃圾输出，重试后合法（fake_chat._plan_seq 控制序列）
    monkeypatch.setattr(llm_mod.chat.__wrapped__ if False else llm_mod, "chat",
                        llm_mod.chat)  # 保持引用
    seq = ["抱歉，我无法完成。", json.dumps(PLAN_RESP, ensure_ascii=False)]
    llm_mod.chat._plan_seq = seq
    try:
        r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
        assert r.status_code == 200, r.text
        assert r.json()["shot_count"] == 3
    finally:
        if hasattr(llm_mod.chat, "_plan_seq"):
            del llm_mod.chat._plan_seq


def test_generate_dirty_values_coerced(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path, plan=PLAN_DIRTY)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2
    # 越界景别/机位/模式回落安全默认；时长钳制 1-15
    assert items[0]["shot_size"] == "中景"
    assert items[0]["camera_movement"] == "固定"
    assert items[0]["mode"] == "t2v"  # 既是非法回落也是首镜头强制
    assert items[0]["duration_sec"] == 15
    assert items[1]["duration_sec"] == 1
    # 裸运动字归一为工作台存储值
    assert items[1]["camera_movement"] == "推镜"
    # 圣经之外的人名保留（不静默丢弃，交由人工核对）
    assert items[0]["character_names"] == ["不存在的人"]


def test_update_item_rejects_bad_duration(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    plan = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate").json()
    iid = plan["items"][0]["id"]
    r = client.put(f"/api/novels/{nid}/episodes/{eid}/shots/{iid}",
                   json={"duration_sec": 99})
    assert r.status_code == 422  # pydantic 层 1-15 拦截
    r = client.put(f"/api/novels/{nid}/episodes/{eid}/shots/{iid}",
                   json={"mode": "魔法"})
    assert r.status_code == 422  # 模式枚举拦截


def test_reorder_moves_items(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    plan = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate").json()
    second = plan["items"][1]
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/{second['id']}/reorder",
                    json={"direction": "up"})
    assert r.status_code == 200
    assert [i["title"] for i in r.json()["items"]] == ["对峙", "入林", "喝止"]
    # 边缘拒绝
    first = r.json()["items"][0]
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/{first['id']}/reorder",
                    json={"direction": "up"})
    assert r.status_code == 400
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/{second['id']}/reorder",
                    json={"direction": "sideways"})
    assert r.status_code == 422


# =====================================================================
# 交接
# =====================================================================
def test_handoff_creates_structured_project(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    # 未审核 → 400
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r.status_code == 400 and "审核" in r.json()["detail"]

    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve", json={})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r.status_code == 200, r.text
    assert r.json()["shot_count"] == 3 and r.json()["character_count"] == 3
    pid = r.json()["project_id"]

    from app import storyboard as sb
    from app import assets as assets_mod
    scenes = sb.list_scenes(pid)
    assert [s["title"] for s in scenes] == ["竹林清晨", "剑拔弩张"]
    assert scenes[0]["description"] == "薄雾青竹，剑气纵横"  # 圣经场景描述（合并后取更全描述）
    shots = [s for sc in scenes for s in sc["shots"]]
    assert len(shots) == 3
    assert [s["shot_size"] for s in shots] == ["远景", "中景", "全景"]
    assert [s["mode"] for s in shots] == ["t2v", "i2v", "continuation"]
    assert [s["duration_s"] for s in shots] == [4.0, 5.0, 3.0]
    assert shots[1]["dialogue"] == "你终于来了。"
    assert "情绪：紧张" in (shots[1]["note"] or "")
    # prompt 带圣经人物外貌/场景描述
    assert "青衫" in shots[0]["prompt"] and "薄雾青竹" in shots[0]["prompt"]
    # 角色卡自动建档 + character_ids 关联
    chars = {c["name"]: c for c in assets_mod.list_characters(pid)}
    assert set(chars) == {"林川", "苏檸", "长老"}
    assert chars["林川"]["appearance"] == "青衫" and chars["林川"]["outfit"] == "青衫"
    assert set(shots[1]["character_ids"]) == {chars["林川"]["id"], chars["苏檸"]["id"]}
    # 不自动启动生成：没有任务
    assert all(not s.get("job_id") for s in shots)

    # 幂等：重复交接同一项目，按审核态重建
    r2 = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r2.status_code == 200 and r2.json()["project_id"] == pid
    assert r2.json()["rebuilt"] is True
    scenes2 = sb.list_scenes(pid)
    assert sum(len(s["shots"]) for s in scenes2) == 3  # 没有翻倍


def test_handoff_refuses_when_project_has_outputs(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve", json={})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    pid = r.json()["project_id"]
    # 在项目里造一个镜头版本（模拟已生成成果）
    from app import storyboard as sb
    shot = sb.list_scenes(pid)[0]["shots"][0]
    sb.create_version(shot["id"], {"id": "job_test", "engine": "test"},
                      "http://x/v.mp4")
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r.status_code == 400 and "制作成果" in r.json()["detail"]


def test_handoff_reuses_script_project(client, fresh_db, monkeypatch, tmp_path):
    """剧本已交接过项目时，分镜交接落在同一项目（替换剧本导入的基础分镜）。"""
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/script/handoff")
    script_pid = r.json()["project_id"]
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve", json={})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r.status_code == 200
    assert r.json()["project_id"] == script_pid  # 同一项目
    from app import storyboard as sb
    scenes = sb.list_scenes(script_pid)
    assert [s["title"] for s in scenes] == ["竹林清晨", "剑拔弩张"]  # 结构已替换


def test_plan_versions_endpoint_404(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    r = client.get(f"/api/novels/{nid}/episodes/{eid}/shots/versions/99")
    assert r.status_code == 404


# =====================================================================
# 级联与兼容
# =====================================================================
def test_delete_cascades(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve", json={})

    from app.db import db
    def count(table):
        return db.fetch_one(f"SELECT COUNT(*) AS c FROM {table}")["c"]

    assert count("novel_shot_plans") == 1
    assert count("novel_shot_items") == 3
    assert count("novel_shot_plan_versions") == 1
    # 删分集 → 计划/镜头/版本全清（FK 级联）
    r = client.delete(f"/api/novels/{nid}/episodes/{eid}")
    assert r.status_code == 204
    assert count("novel_shot_plans") == 0
    assert count("novel_shot_items") == 0
    assert count("novel_shot_plan_versions") == 0
    # 删小说 → 分镜相关表不留孤儿
    _setup_approved_script(client, monkeypatch, tmp_path,
                           text=NOVEL_TEXT + "\n\n第三章 收招\n白光闪过，大剑归鞘。\n")
    r = client.delete(f"/api/novels/{nid}")
    assert r.status_code == 204
    assert count("novel_shot_plans") == 0


def test_legacy_apis_unaffected(client, fresh_db, monkeypatch, tmp_path):
    """旧接口兼容：小说/章节/圣经/分集/剧本链路与既有项目接口不受影响。"""
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert client.get("/api/novels").status_code == 200
    assert client.get(f"/api/novels/{nid}/chapters").status_code == 200
    assert client.get(f"/api/novels/{nid}/bible").status_code == 200
    assert client.get(f"/api/novels/{nid}/episodes").status_code == 200
    assert client.get(f"/api/novels/{nid}/episodes/{eid}/script").status_code == 200
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/health").json() == {"ok": True}
