"""3A HTTP 接口测试：保护门禁在 REST 层的错误映射（409/422/400）。

与 test_protection_gates.py 的分工：那边测数据层行为，这边测
FastAPI 路由的状态码与响应体契约（前端依赖的结构）。
"""
import pytest
from fastapi.testclient import TestClient

from app import storyboard as sb

SCRIPT_A = "场景一：教室\n同学走进教室（中景, 固定, 3秒）\n场景二：操场\n大家在跑步"
SCRIPT_B = "场景一：雨夜\n甲独自走在街上（远景, 跟, 4秒）\n乙追上来"
GOOD_PROMPT = "雨夜街头，霓虹灯下的长镜头，湿地反光"


@pytest.fixture()
def client(fresh_db):
    from app.main import app

    # 不进 lifespan：不起 worker 线程，这些用例只打数据路由
    return TestClient(app)


@pytest.fixture()
def outputs(tmp_path, monkeypatch):
    import app.config as config

    d = tmp_path / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "OUTPUTS_DIR", d)
    return d


def _mk_project(client):
    r = client.post("/api/projects", json={"name": "http3a", "description": ""})
    assert r.status_code == 201
    return r.json()["id"]


def _import(client, project_id, text=SCRIPT_A, mode="rebuild", **extra):
    body = {"text": text, "mode": mode, **extra}
    return client.post(f"/api/projects/{project_id}/script/import", json=body)


def _mk_version(fresh_db, shot_id, video_name="v.mp4", with_file=True, outputs=None):
    job = fresh_db.create_job({
        "engine": "fake", "prompt": GOOD_PROMPT, "width": 512, "height": 512,
        "num_frames": 16, "fps": 8, "steps": 25, "guidance": 7.5, "seed": 7,
        "shot_id": shot_id,
    })
    fresh_db.update_job(job["id"], status="done")
    if with_file:
        assert outputs is not None
        (outputs / video_name).write_bytes(b"\x00\x00\x00\x18ftypmp42" * 16)
    return sb.create_version(shot_id, job, f"/outputs/{video_name}", "")


# =====================================================================
# 审核接口
# =====================================================================
def test_design_review_http_flow(client, fresh_db, outputs):
    pid = _mk_project(client)
    _import(client, pid)
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    shot = scenes[0]["shots"][0]
    sid = shot["id"]

    # 提示词过短 → 设计审核阻断 422，带 issues/score
    client.patch(f"/api/shots/{sid}", json={"prompt": "短"})
    r = client.post(f"/api/shots/{sid}/review",
                    json={"action": "approve", "stage": "design"})
    assert r.status_code == 422, r.text
    body = r.json()["detail"]
    assert body["issues"] and body["score"]["level"] == "error"

    # 补提示词后通过 → review_stage=design_ok
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    r = client.post(f"/api/shots/{sid}/review",
                    json={"action": "approve", "stage": "design"})
    assert r.status_code == 200
    assert r.json()["review_stage"] == "design_ok"
    assert r.json()["status"] == "draft"


def test_video_review_http_flow(client, fresh_db, outputs):
    pid = _mk_project(client)
    _import(client, pid)
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    shot = scenes[0]["shots"][0]
    sid = shot["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})

    # 无版本审核通过 → 400
    r = client.post(f"/api/shots/{sid}/review", json={"action": "approve"})
    assert r.status_code == 400, r.text

    # 文件缺失的版本 → 422 阻断（QA 前置）
    ver = _mk_version(fresh_db, sid, "missing.mp4", with_file=False, outputs=outputs)
    sb.update_shot(sid, current_version_id=ver["id"])
    sb.set_shot_status(sid, "review", review_stage="video")
    r = client.post(f"/api/shots/{sid}/review", json={"action": "approve"})
    assert r.status_code == 422, r.text
    codes = {i["code"] for i in r.json()["detail"]["issues"]}
    assert "video_missing" in codes
    # 阻断后状态/指针不变
    after = client.get(f"/api/shots/{sid}").json()
    assert after["status"] == "review" and after["current_version_id"] == ver["id"]

    # 文件就位 → 通过
    (outputs / "missing.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" * 16)
    r = client.post(f"/api/shots/{sid}/review",
                    json={"action": "approve", "comment": "OK"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved" and r.json()["review_stage"] == ""


def test_review_stale_submission_http(client, fresh_db, outputs):
    pid = _mk_project(client)
    _import(client, pid)
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    ver = _mk_version(fresh_db, sid, "v.mp4", True, outputs)
    sb.update_shot(sid, current_version_id=ver["id"])
    sb.set_shot_status(sid, "review")
    r = client.post(f"/api/shots/{sid}/review", json={
        "action": "approve", "expected_version_id": "ver_not_current",
    })
    assert r.status_code == 409, r.text
    assert "过期" in r.json()["detail"]["message"] or "变化" in r.json()["detail"]["message"]


def test_adopt_and_delete_version_http(client, fresh_db, outputs):
    pid = _mk_project(client)
    _import(client, pid)
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    ver = _mk_version(fresh_db, sid, "v.mp4", True, outputs)
    r = client.post(f"/api/shots/{sid}/versions/{ver['id']}/adopt")
    assert r.status_code == 200
    assert r.json()["current_version_id"] == ver["id"]

    # 删当前版本（无时间线引用）→ 成功，指针回退
    r = client.delete(f"/api/shots/{sid}/versions/{ver['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/shots/{sid}").json()["current_version_id"] == ""


# =====================================================================
# 引用保护接口
# =====================================================================
def test_timeline_reference_blocks_http(client, fresh_db, outputs):
    pid = _mk_project(client)
    _import(client, pid)
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    sid = scenes[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    ver = _mk_version(fresh_db, sid, "v.mp4", True, outputs)
    sb.update_shot(sid, current_version_id=ver["id"])
    sb.set_shot_status(sid, "review")
    client.post(f"/api/projects/{pid}/timeline/sync")
    assert client.get(f"/api/projects/{pid}/timeline").json()

    # 换采用版本 → 409 + 引用明细
    ver2 = _mk_version(fresh_db, sid, "v2.mp4", True, outputs)
    r = client.post(f"/api/shots/{sid}/review",
                    json={"action": "approve", "version_id": ver2["id"]})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["references"]["timeline_items"]
    r = client.post(f"/api/shots/{sid}/versions/{ver2['id']}/adopt")
    assert r.status_code == 409, r.text
    # 指针未被静默更换
    assert client.get(f"/api/shots/{sid}").json()["current_version_id"] == ver["id"]

    # 删镜头 / 删场景 / 删项目 → 409
    assert client.delete(f"/api/shots/{sid}").status_code == 409
    assert client.delete(f"/api/scenes/{scenes[0]['id']}").status_code == 409
    assert client.delete(f"/api/projects/{pid}").status_code == 409

    # 删被引用的任务 → 409
    r = client.delete(f"/api/jobs/{ver['job_id']}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["references"]["versions"]


# =====================================================================
# 导入保护接口
# =====================================================================
def test_import_protection_http(client, fresh_db, outputs):
    pid = _mk_project(client)
    _import(client, pid)
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    sid = scenes[0]["shots"][0]["id"]
    ver = _mk_version(fresh_db, sid, "v.mp4", True, outputs)
    sb.update_shot(sid, current_version_id=ver["id"])
    sb.set_shot_status(sid, "review")
    client.post(f"/api/projects/{pid}/timeline/sync")

    # 预览：返回 content_hash + project_revision，不写库
    pv = client.post(f"/api/projects/{pid}/script/import",
                     json={"text": SCRIPT_B, "mode": "preview"}).json()
    assert pv["mode"] == "preview" and pv["content_hash"]

    # 破坏性重建未确认 → 409 + loss 明细
    r = _import(client, pid, SCRIPT_B,
                expected_revision=pv["project_revision"],
                content_hash=pv["content_hash"])
    assert r.status_code == 409, r.text
    loss = r.json()["detail"]["loss"]
    assert loss["version_count"] >= 1 and loss["timeline_count"] >= 1

    # 确认后重建 → 200 + 备份路径 + revision 自增
    r = _import(client, pid, SCRIPT_B, confirm=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == pv["project_revision"] + 1
    import pathlib
    assert pathlib.Path(body["backup"]).is_file()
    titles = [s["title"] for s in client.get(f"/api/projects/{pid}/scenes").json()]
    assert titles == ["雨夜"]

    # 过期预览提交 → 409
    pv2 = client.post(f"/api/projects/{pid}/script/import",
                      json={"text": SCRIPT_A, "mode": "preview"}).json()
    _import(client, pid, SCRIPT_A, mode="append")  # revision 再 +1
    r = _import(client, pid, SCRIPT_A,
                expected_revision=pv2["project_revision"],
                content_hash=pv2["content_hash"])
    assert r.status_code == 409, r.text

    # content_hash 不匹配 → 400
    r = _import(client, pid, SCRIPT_A, content_hash="0" * 64)
    assert r.status_code == 400, r.text
