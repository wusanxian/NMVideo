"""PR-N06：分镜计划 ↔ 故事圣经一致性校验。

覆盖：全匹配通过 / 未知人物警告 / 别名解析提示 / 圣经版本过期
（stale）/ 草稿态提示 / 计划缺失 404 / 交接与提交批次附带警告。
"""
import json

import pytest

from test_novel_shot_plan import _patch_llm, _setup_approved_script
from test_novel_submit import (ENGINE, SUBMIT_PLAN, _fake_creator,
                               _setup_ready)


def _val(client, nid, eid):
    r = client.get(f"/api/novels/{nid}/episodes/{eid}/shots/validate")
    assert r.status_code == 200, r.text
    return r.json()


def _items(client, nid, eid):
    return client.get(
        f"/api/novels/{nid}/episodes/{eid}/shots").json()["items"]


# =====================================================================
# 基础校验
# =====================================================================
def test_validate_all_matched(client, fresh_db, monkeypatch, tmp_path):
    """默认夹具：镜头人物全部命中圣经标准名 → 无警告。"""
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    v = _val(client, nid, eid)
    assert v["ok"] is True and v["stale"] is False
    assert v["warnings"] == []
    assert v["unmatched_characters"] == []
    # item_issues 只允许「场景组标题信息项」——人物全命中、无别名
    for iss in v["item_issues"]:
        assert iss["unknown_characters"] == []
        assert iss["alias_resolved"] == []
    assert v["checked_items"] == plan["shot_count"]
    # 生成计划时冻结了当时圣经版本（夹具圣经未审核 → v0）
    assert v["plan_bible_version"] == 0 and v["bible_version"] == 0


def test_validate_unknown_character_warning(client, fresh_db, monkeypatch,
                                            tmp_path):
    """镜头引用圣经中不存在的人名 → 全局警告 + 逐镜定位。"""
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    it = _items(client, nid, eid)[0]
    r = client.put(
        f"/api/novels/{nid}/episodes/{eid}/shots/{it['id']}",
        json={"character_names": ["林川", "无名路人"]})
    assert r.status_code == 200, r.text

    v = _val(client, nid, eid)
    assert v["ok"] is False
    assert v["unmatched_characters"] == ["无名路人"]
    assert any("无名路人" in w for w in v["warnings"])
    issue = next(i for i in v["item_issues"] if i["seq"] == it["seq"])
    assert issue["unknown_characters"] == ["无名路人"]


def test_validate_alias_resolution_hint(client, fresh_db, monkeypatch,
                                        tmp_path):
    """圣经人物配置别名后，镜头用别名 → 提示归一到标准名。"""
    from app import novel_bible as bible_mod

    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    bible = client.get(f"/api/novels/{nid}/bible").json()
    lin = next(c for c in bible["characters"] if c["name"] == "林川")
    assert client.put(
        f"/api/novels/{nid}/bible/characters/{lin['id']}",
        json={"aliases": ["林小哥"]}).status_code == 200

    it = _items(client, nid, eid)[0]
    assert client.put(
        f"/api/novels/{nid}/episodes/{eid}/shots/{it['id']}",
        json={"character_names": ["林小哥"]}).status_code == 200

    v = _val(client, nid, eid)
    # 别名能解析 → 不算未知人物
    assert v["unmatched_characters"] == []
    assert any("林小哥→林川" in w for w in v["warnings"])
    issue = next(i for i in v["item_issues"] if i["seq"] == it["seq"])
    assert issue["alias_resolved"] == [{"name": "林小哥", "canonical": "林川"}]


def test_validate_scene_mismatch_info_only(client, fresh_db, monkeypatch,
                                           tmp_path):
    """场景组标题与圣经场景名不相等是常态（LLM 组织分组）→ 仅逐镜信息，
    不产生全局警告，ok 不受影响。"""
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    # 夹具圣经场景「竹林」vs 计划场景组「竹林清晨」等 → 必然不匹配
    v = _val(client, nid, eid)
    assert v["ok"] is True and v["warnings"] == []
    assert v["unmatched_scenes"], "夹具场景标题应至少一个不在圣经中"
    # item_issues 只在有可提示项时出现条目，且 unknown_characters 为空
    for iss in v["item_issues"]:
        assert iss["unknown_characters"] == []
        assert iss["alias_resolved"] == []
        assert iss["unknown_scenes"]


# =====================================================================
# 圣经 → 分镜方向（版本过期 / 草稿态）
# =====================================================================
def test_validate_stale_after_bible_version_bump(client, fresh_db,
                                                 monkeypatch, tmp_path):
    """圣经审核出 v2、分镜冻结于 v1 → stale + 版本警告。"""
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    # 圣经审核 → v1
    assert client.post(
        f"/api/novels/{nid}/bible/approve", json={}).status_code == 200
    # 以 v1 圣经重新生成分镜（冻结 v1）
    _patch_llm(monkeypatch, tmp_path, plan=SUBMIT_PLAN)
    assert client.post(
        f"/api/novels/{nid}/episodes/{eid}/shots/generate").status_code == 200
    v = _val(client, nid, eid)
    assert v["plan_bible_version"] == 1 and v["bible_version"] == 1
    assert v["stale"] is False and v["ok"] is True

    # 圣经重生成（回到草稿）→ 草稿态警告（分镜生成于已审核圣经之后）
    assert client.post(
        f"/api/novels/{nid}/bible/generate").status_code == 200
    v = _val(client, nid, eid)
    assert v["stale"] is False
    assert any("草稿态" in w for w in v["warnings"])

    # 再次审核 → v2 ≠ 分镜冻结的 v1 → stale
    assert client.post(
        f"/api/novels/{nid}/bible/approve", json={}).status_code == 200
    v = _val(client, nid, eid)
    assert v["stale"] is True and v["bible_version"] == 2
    assert any("圣经已更新" in w and "v1" in w and "v2" in w
               for w in v["warnings"])


def test_validate_404_without_plan(client, fresh_db, monkeypatch, tmp_path):
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    r = client.get(f"/api/novels/{nid}/episodes/{eid}/shots/validate")
    assert r.status_code == 404 and "尚未生成" in r.json()["detail"]


# =====================================================================
# 交接 / 提交批次附带警告
# =====================================================================
def test_handoff_and_submit_carry_warnings(client, fresh_db, monkeypatch,
                                           tmp_path):
    """交接响应与提交批次都冻结一致性警告（不阻断操作本身）。"""
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    it = _items(client, nid, eid)[0]
    assert client.put(
        f"/api/novels/{nid}/episodes/{eid}/shots/{it['id']}",
        json={"character_names": ["林川", "无名路人"]}).status_code == 200
    assert client.post(
        f"/api/novels/{nid}/episodes/{eid}/shots/approve",
        json={}).status_code == 200

    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r.status_code == 200, r.text
    assert any("无名路人" in w for w in r.json()["warnings"])

    from app import novel_submit as ns
    monkeypatch.setattr(ns, "_create_job_for_shot",
                        _fake_creator(fresh_db, {}))
    monkeypatch.setattr(ns, "_ensure_watcher", lambda: None)
    r = client.post(
        f"/api/novels/{nid}/episodes/{eid}/shots/submit",
        json={"engine": ENGINE})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert any("无名路人" in w for w in sub["warnings"])
    # GET 读路径同样解出警告
    lst = client.get(
        f"/api/novels/{nid}/episodes/{eid}/shots/submissions").json()
    assert lst[0]["warnings"] == sub["warnings"]
