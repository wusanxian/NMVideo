import json

import pytest

from app import storyboard as sb, tracks
from app.dialogue_timing import schedule, subtitles_for_cues


def test_dialogue_schedule_extends_and_shifts_next_shot():
    layout = [{"shot_id": "a", "shot_title": "对话", "start_s": 0, "end_s": 1},
              {"shot_id": "b", "start_s": 1, "end_s": 2}]
    voices = [{"shot_id": "a", "duration_s": 0.8, "text": "你好。再见！"},
              {"shot_id": "a", "duration_s": 0.7, "text": "好。"}]
    with pytest.raises(ValueError, match="配音需要"):
        schedule(layout, voices)
    arranged, cues = schedule(layout, voices, extend=True)
    assert arranged[0]["end_s"] == 1.5
    assert arranged[1]["start_s"] == 1.5
    assert arranged[1]["end_s"] == 2.5
    assert cues[1]["start_s"] == cues[0]["end_s"] == 0.8
    subtitles = subtitles_for_cues(cues)
    assert [s["text"] for s in subtitles] == ["你好。", "再见！", "好。"]
    assert subtitles[-1]["end_s"] == 1.5
    assert layout[1]["start_s"] == 1  # 不修改存储快照


def test_same_second_voices_keep_creation_order(fresh_db):
    project = sb.create_project("顺序")
    shot = sb.create_shot(sb.create_scene(project["id"], "场景")["id"])
    first = tracks.create_voice(shot["id"], text="甲", duration_s=1)
    second = tracks.create_voice(shot["id"], text="乙", duration_s=1)
    assert [v["id"] for v in tracks.list_voices_of_shot(shot["id"])] == [first["id"], second["id"]]


def test_export_freezes_canvas_settings_and_validates(client, monkeypatch):
    import app.routers.storyboard as router
    monkeypatch.setattr(router, "start_export", lambda _: None)
    pid = client.post("/api/projects", json={"name": "竖屏"}).json()["id"]
    result = client.post(f"/api/projects/{pid}/export", json={
        "canvas": "portrait", "fit": "cover", "extend_dialogue": False, "fps": 30})
    assert result.status_code == 201
    assert json.loads(result.json()["manifest"])["output"] == {
        "canvas": "portrait", "fit": "cover", "extend_dialogue": False}
    assert client.post(f"/api/projects/{pid}/export", json={"canvas": "invalid"}).status_code == 422


def test_tts_does_not_silently_truncate():
    from app.tts import _clean_text
    with pytest.raises(ValueError, match="分句"):
        _clean_text("长" * 301)


def test_auto_subtitles_use_voice_text_and_times(client, monkeypatch):
    pid = client.post("/api/projects", json={"name": "字幕"}).json()["id"]
    scene = sb.create_scene(pid, "场景")
    shot = sb.create_shot(scene["id"], dialogue="不会被当作实际配音")
    tracks.create_voice(shot["id"], duration_s=0.8, text="第一句。")
    tracks.create_voice(shot["id"], duration_s=0.7, text="第二句！")
    monkeypatch.setattr(sb, "timeline_layout", lambda _: [
        {"shot_id": shot["id"], "start_s": 0, "end_s": 1.5, "dialogue": shot["dialogue"]}])
    response = client.post(f"/api/projects/{pid}/subtitles/auto")
    assert response.status_code == 200
    assert [(s["text"], s["start_s"], s["end_s"]) for s in response.json()] == [
        ("第一句。", 0, 0.8), ("第二句！", 0.8, 1.5)]
