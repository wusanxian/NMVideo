"""PR-04（导演工作台）：角色快照冻结——镜头版本锁定生成所用角色数据。

验收对照（图中 PR-04「角色版本锁定」）：
- 生成时冻结角色卡（外貌/服装/LoRA/参考图/音色）到 job 与 shot_versions
- 之后修改角色卡，已生成版本的快照不变（锁定语义，旧版本可复现）
- 未关联角色的镜头版本 characters 为空列表
衔接关系（PR-02 的 continue_from/source_video_version_id/inheritance_stale）
与局部返工（rework_from + 级联）已在既有批次落地，本文件不重复覆盖。
"""
import asyncio
import json
from pathlib import Path

import pytest

from app import queue_manager as qm
from app import storyboard as sb

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x18ftypmp42" * 8
GOOD_PROMPT = "武侠对决，两名剑客面对面站位（中景, 固定, 4秒）"


class _FakeLocalEngine:
    """最小本地引擎：写一帧 PNG 即返回（不触 torch/网络）。"""

    def generate(self, **kwargs):
        frames_dir = Path(kwargs["frames_dir"])
        frames_dir.mkdir(parents=True, exist_ok=True)
        f = frames_dir / "frame_0001.png"
        f.write_bytes(PNG_BYTES)
        return [f]


@pytest.fixture(autouse=True)
def _clean_queue():
    qm.job_queue._engine_instances.clear()
    qm.job_queue._cancelled.clear()
    yield
    qm.job_queue._engine_instances.clear()
    qm.job_queue._cancelled.clear()


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(qm, "OUTPUTS_DIR", out)
    monkeypatch.setattr(qm, "frames_to_video",
                        lambda *a, **k: Path(a[1]).write_bytes(b"\x00\x00\x00\x18ftypmp42" * 16))
    monkeypatch.setattr(qm, "get_engine", lambda eid: (lambda: _FakeLocalEngine()))
    qm.job_queue._engine_instances.clear()
    return out


def _mk_shot_with_character(client):
    pid = client.post("/api/projects",
                      json={"name": "wuxia", "description": ""}).json()["id"]
    client.post(f"/api/projects/{pid}/script/import",
                json={"text": "场景一：雨夜竹林\n甲乙对决（中景, 固定, 4秒）",
                      "mode": "rebuild"})
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    cid = client.post(f"/api/projects/{pid}/characters", json={
        "name": "剑客甲", "role": "主角", "appearance": "束发，剑眉星目，左颊有疤",
        "outfit": "青色武袍", "lora": "wuxia_hero_v2",
        "reference_images": ["/outputs/refs/hero.png"],
    }).json()["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    # 绑定角色走专用端点（ShotUpdate 无 character_ids 字段，PATCH 会被忽略）
    client.put(f"/api/shots/{sid}/characters", json={"character_ids": [cid]})
    return pid, sid, cid


def test_generation_freezes_character_snapshot(client, fresh_db, fake_env):
    """生成落版本后：version.characters 为入队时的角色快照（可复现）。"""
    pid, sid, cid = _mk_shot_with_character(client)
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "anime"})
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    # 入队冻结：job 行已带快照（生成开始前就固定，不受排队期间改卡影响）
    job = fresh_db.get_job(job_id)
    snap = json.loads(job["characters_snapshot"])
    assert len(snap) == 1
    assert snap[0]["name"] == "剑客甲"
    assert snap[0]["outfit"] == "青色武袍"
    assert snap[0]["lora"] == "wuxia_hero_v2"
    assert snap[0]["reference_images"] == ["/outputs/refs/hero.png"]

    asyncio.run(qm.job_queue._run_job(job_id))
    assert fresh_db.get_job(job_id)["status"] == "done"

    shot = client.get(f"/api/shots/{sid}").json()
    assert shot["versions"], "生成未落版本"
    ver = shot["versions"][0]
    assert ver["characters"] == snap, "版本快照应与入队冻结一致"
    assert any(c["name"] == "剑客甲" for c in ver["characters"])


def test_character_edits_do_not_touch_old_versions(client, fresh_db, fake_env):
    """锁定语义：改角色卡后，旧版本快照保持不变。"""
    from app import assets as assets_mod

    pid, sid, cid = _mk_shot_with_character(client)
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "anime"})
    job_id = r.json()["id"]
    asyncio.run(qm.job_queue._run_job(job_id))

    before = client.get(f"/api/shots/{sid}").json()["versions"][0]["characters"]
    # 换装 + 换 LoRA（现实中最常见的「角色迭代」）
    assets_mod.update_character(cid, outfit="白色武袍", lora="wuxia_hero_v3")

    after = client.get(f"/api/shots/{sid}").json()["versions"][0]["characters"]
    assert after == before, "改角色卡不应影响已生成版本的快照"
    assert after[0]["outfit"] == "青色武袍"
    assert after[0]["lora"] == "wuxia_hero_v2"


def test_version_without_characters_is_empty_list(client, fresh_db, fake_env):
    """未关联角色：版本 characters 为空列表（不是 None/缺字段）。"""
    pid = client.post("/api/projects",
                      json={"name": "solo", "description": ""}).json()["id"]
    client.post(f"/api/projects/{pid}/script/import",
                json={"text": "场景一：山道\n独行侠赶路（远景, 固定, 3秒）",
                      "mode": "rebuild"})
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "anime"})
    job_id = r.json()["id"]
    asyncio.run(qm.job_queue._run_job(job_id))

    ver = client.get(f"/api/shots/{sid}").json()["versions"][0]
    assert ver["characters"] == []
