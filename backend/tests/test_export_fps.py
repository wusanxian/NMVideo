"""PR-06 剩余：导出帧率参数化（历史硬编码 fps=16 与 24fps 质量档位不一致）。

改动链：projects.fps（默认 24）→ exports.fps（创建时冻结）→
run_export 传给 concat_videos（旧记录 fps=0 回落项目帧率再回落 24）。
"""
import pytest


# =====================================================================
# API 层：默认帧率 / 项目帧率 / 显式指定 / 非法值
# =====================================================================
def _mk_project(client, name="fps-proj"):
    return client.post("/api/projects", json={"name": name, "description": ""}).json()["id"]


def test_project_default_fps_24(client):
    pid = _mk_project(client)
    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["fps"] == 24


def test_export_defaults_to_project_fps(client):
    pid = _mk_project(client)
    exp = client.post(f"/api/projects/{pid}/export", json={}).json()
    assert exp["fps"] == 24, "未显式指定 → 用项目帧率（默认 24）"


def test_export_uses_project_fps_after_patch(client):
    pid = _mk_project(client)
    r = client.patch(f"/api/projects/{pid}", json={"fps": 30})
    assert r.status_code == 200, r.text
    assert r.json()["fps"] == 30
    exp = client.post(f"/api/projects/{pid}/export", json={}).json()
    assert exp["fps"] == 30


def test_export_explicit_fps_freezes(client):
    pid = _mk_project(client)
    exp = client.post(f"/api/projects/{pid}/export", json={"fps": 16}).json()
    assert exp["fps"] == 16, "显式指定优先于项目帧率（创建时冻结）"


def test_export_rejects_invalid_fps(client):
    pid = _mk_project(client)
    r = client.post(f"/api/projects/{pid}/export", json={"fps": 90})
    assert r.status_code == 422, "帧率必须在 8-60（pydantic 校验）"
    r = client.post(f"/api/projects/{pid}/export", json={"fps": 0})
    assert r.status_code == 422


# =====================================================================
# Worker 层：run_export 把冻结帧率传给 concat_videos
# =====================================================================
def test_run_export_passes_frozen_fps_to_concat(fresh_db, tmp_path, monkeypatch):
    import app.export as export_mod
    import app.video as video_mod
    from app import storyboard as sb

    project = sb.create_project("fps-worker")
    exp = sb.create_export(project["id"], fps=30)
    sb.update_export(exp["id"], manifest="")  # 此用例仅检查兼容旧导出的帧率传递

    fake_video = tmp_path / "seg.mp4"
    fake_video.write_bytes(b"\x00\x00")

    monkeypatch.setattr(export_mod, "_url_to_path", lambda url: fake_video)
    monkeypatch.setattr(
        export_mod.sb, "timeline_layout",
        lambda pid: [{"shot_id": "s1", "shot_title": "A", "video_url": "/outputs/x.mp4",
                      "trim_in_eff": 0, "trim_out_s": 0, "transition": "none"}],
    )
    monkeypatch.setattr(video_mod, "probe_resolution", lambda p: (512, 512))

    captured = {}

    def _fake_concat(items, out_path, width=0, height=0, fps=16, on_progress=None):
        captured["fps"] = fps
        out_path.write_bytes(b"video")
        return out_path

    monkeypatch.setattr(export_mod, "concat_videos", _fake_concat)
    monkeypatch.setattr(export_mod, "_mix_audio", lambda pid, layout, out: False)
    monkeypatch.setattr(export_mod, "_subtitle_path", lambda pid, tmp: None)
    monkeypatch.setattr(export_mod, "_final_mux",
                        lambda video, audio, srt, out, on_progress=None: out.write_bytes(b"ok"))

    export_mod.run_export(exp["id"])

    assert captured.get("fps") == 30, "run_export 必须把冻结帧率传给 concat（不再硬编码 16）"
    final = sb.get_export(exp["id"])
    assert final["status"] == "done"


def test_run_export_legacy_row_falls_back_to_project_fps(fresh_db, tmp_path, monkeypatch):
    """旧导出记录 fps=0（迁移默认）→ 回落项目帧率；项目也无效 → 24。"""
    import app.export as export_mod
    import app.video as video_mod
    from app import storyboard as sb

    project = sb.create_project("fps-legacy")  # fps=24
    exp = sb.create_export(project["id"])
    sb.update_export(exp["id"], fps=0, manifest="")  # 模拟迁移前的旧记录

    fake_video = tmp_path / "seg.mp4"
    fake_video.write_bytes(b"\x00\x00")

    monkeypatch.setattr(export_mod, "_url_to_path", lambda url: fake_video)
    monkeypatch.setattr(
        export_mod.sb, "timeline_layout",
        lambda pid: [{"shot_id": "s1", "shot_title": "A", "video_url": "/outputs/x.mp4",
                      "trim_in_eff": 0, "trim_out_s": 0, "transition": "none"}],
    )
    monkeypatch.setattr(video_mod, "probe_resolution", lambda p: (512, 512))

    captured = {}

    def _fake_concat(items, out_path, width=0, height=0, fps=16, on_progress=None):
        captured["fps"] = fps
        out_path.write_bytes(b"video")
        return out_path

    monkeypatch.setattr(export_mod, "concat_videos", _fake_concat)
    monkeypatch.setattr(export_mod, "_mix_audio", lambda pid, layout, out: False)
    monkeypatch.setattr(export_mod, "_subtitle_path", lambda pid, tmp: None)
    monkeypatch.setattr(export_mod, "_final_mux",
                        lambda video, audio, srt, out, on_progress=None: out.write_bytes(b"ok"))

    export_mod.run_export(exp["id"])

    assert captured.get("fps") == 24, "旧记录回落项目帧率 24"
