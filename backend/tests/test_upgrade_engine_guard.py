"""PR-N08：升级链路引擎护栏软化（AnimeDiff）+ 档位可选。

背景：AnimeDiff 有 32 帧 / 640×640 面积护栏，旧升级链路对 anime 引擎
直接 400（预览无法升级成高清候选）——本批把 anime 升级钳到引擎上限，
其他引擎保持严格校验不回归。
"""

import json


def _make_preview(client, fresh_db, engine="anime", preset="standard"):
    """建一条已完成的预览任务（参数走真实 normalize_generation）。"""
    r = client.post("/api/jobs", json={
        "engine": engine, "prompt": "竹林少年", "preview": True,
        "preset": preset,
    })
    assert r.status_code == 201, r.text
    preview = r.json()
    fresh_db.update_job(preview["id"], status="done")
    return preview


# =====================================================================
# AnimeDiff：钳制而非 400
# =====================================================================
def test_anime_upgrade_clamps_to_engine_limit(client, fresh_db):
    """anime 预览升级到 standard（832×480×81帧）→ 钳到 32 帧，分辨率保留。

    注意：anime 预览创建时带 standard 档会被 normalize_generation 直接 400
    （线上失败任务即此路径），所以升级目标档位只能通过升级时的 picker 传入。
    """
    preview = _make_preview(client, fresh_db, "anime", "")
    # 预览档 416×256；anime 分支取偶帧（PREVIEW_OVERRIDE 17 → 16）
    assert preview["width"] == 416 and preview["num_frames"] == 16

    r = client.post(f"/api/jobs/{preview['id']}/upgrade",
                    json={"preset": "standard"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert not job["preview"] and job["parent_job_id"] == preview["id"]
    assert job["width"] == 832 and job["height"] == 480  # 面积在护栏内，保留
    assert job["num_frames"] == 32  # 81 → 钳到引擎上限
    assert "帧数 81→32" in job["upgrade_clamped"]
    assert job["preset"] == "standard"


def test_anime_upgrade_clamps_resolution(client, fresh_db):
    """fine 档 720p 超 640×640 面积 → 等比缩到面积内。"""
    preview = _make_preview(client, fresh_db, "anime", "")
    r = client.post(f"/api/jobs/{preview['id']}/upgrade", json={"preset": "fine"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["width"] * job["height"] <= 640 * 640
    assert job["num_frames"] == 32
    assert "分辨率" in job["upgrade_clamped"]


def test_anime_upgrade_preset_picker(client, fresh_db):
    """升级时可选目标档位（旧预览 preset 为空也能升级）。"""
    preview = _make_preview(client, fresh_db, "anime", "")
    # 模拟旧预览：没有 generation_target
    fresh_db.update_job(preview["id"], generation_target="")
    r = client.post(f"/api/jobs/{preview['id']}/upgrade",
                    json={"preset": "standard"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["preset"] == "standard" and job["width"] == 832
    assert job["num_frames"] == 32


def test_anime_upgrade_without_target_and_preset_still_400(client, fresh_db):
    """旧预览无目标参数且未选档位 → 明确 400（保留原语义）。"""
    preview = _make_preview(client, fresh_db, "anime", "")
    fresh_db.update_job(preview["id"], generation_target="")
    r = client.post(f"/api/jobs/{preview['id']}/upgrade", json={})
    assert r.status_code == 400 and "目标参数" in r.json()["detail"]


def test_anime_upgrade_requires_done_preview(client, fresh_db):
    """未完成 / 非预览任务不可升级（原语义保持）。"""
    preview = _make_preview(client, fresh_db, "anime", "")
    r = client.post(f"/api/jobs/{preview['id']}/upgrade", json={})
    assert r.status_code == 201
    # 再建一条排队中的预览
    fresh_db.update_job(preview["id"], status="pending")
    r = client.post(f"/api/jobs/{preview['id']}/upgrade", json={})
    assert r.status_code == 400 and "预览任务" in r.json()["detail"]


# =====================================================================
# 其他引擎不回归
# =====================================================================
def test_wan22_upgrade_unchanged(client, fresh_db):
    """wan22 无 anime 护栏：fine 档原样升级 1280×720×81 帧。"""
    preview = _make_preview(client, fresh_db, "wan22", "fine")
    r = client.post(f"/api/jobs/{preview['id']}/upgrade", json={})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["width"] == 1280 and job["height"] == 720
    assert job["num_frames"] == 81 and job["steps"] == 40
    assert not job.get("upgrade_clamped")
