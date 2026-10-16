"""第二批功能端到端测试：角色资产 / TTS / 多轨 / 审核历史 / 返工 / 导出混音

用法：先启动后端（uvicorn app.main:app --port 8000），再运行本脚本。
导出测试用 ffmpeg testsrc 造小视频，不依赖真实生成。
"""
import json
import subprocess
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"
OUTPUTS = Path(__file__).resolve().parent.parent / "outputs"


def call(method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read()
        return payload if raw else json.loads(payload or b"null")


def main():
    print("== 1. 创建项目与角色资产 ==")
    proj = call("POST", "/projects", {"name": "E2E第二批", "description": "资产/配音/多轨测试"})
    pid = proj["id"]
    print("project:", pid)

    hero = call("POST", f"/projects/{pid}/characters", {
        "name": "林越", "role": "主角",
        "appearance": "young man, black hair, sharp eyes",
        "outfit": "dark blue jacket",
        "voice_id": "zh-CN-YunxiNeural", "voice_name": "云希",
        "lora": "", "expressions": [{"name": "冷静", "prompt": "calm expression"}],
        "reference_images": [],
        "notes": "主角，外冷内热",
    })
    print("character:", hero["id"], hero["name"], "| voice:", hero["voice_name"])
    assert hero["expressions"][0]["name"] == "冷静"

    companion = call("POST", f"/projects/{pid}/characters", {
        "name": "苏晚晴", "role": "配角",
        "appearance": "young woman, silver hair, red ribbon",
        "outfit": "white dress",
        "voice_id": "zh-CN-XiaoxiaoNeural",
    })
    chars = call("GET", f"/projects/{pid}/characters")
    assert len(chars) == 2, chars

    print("== 2. 场景 + 分镜 + 绑定角色 ==")
    scene = call("POST", f"/projects/{pid}/scenes",
                 {"title": "雨夜街道", "environment": "霓虹街道", "lighting": "冷色霓虹",
                  "layout": "长街纵深", "style": "赛博动漫"})
    shots = []
    for i, (title, dlg) in enumerate([
        ("长街独行", "林越：这天要变了。"),
        ("回眸", "苏晚晴：你果然在这里。"),
        ("并肩", ""),
    ]):
        sh = call("POST", f"/scenes/{scene['id']}/shots", {
            "title": title, "dialogue": dlg, "shot_size": "全景",
            "camera_movement": "移镜", "duration_s": 2.5,
            "prompt": f"cinematic rainy neon street, character walking, shot {i}",
        })
        call("PUT", f"/shots/{sh['id']}/characters",
             {"character_ids": [hero["id"]] if i != 1 else [hero["id"], companion["id"]]})
        shots.append(call("GET", f"/shots/{sh['id']}"))
    assert shots[0]["character_ids"] == [hero["id"]]
    assert len(shots[1]["character_ids"]) == 2
    print("shots bound:", [len(s["character_ids"]) for s in shots])

    print("== 3. TTS 配音（角色音色） ==")
    voices = call("GET", "/tts/voices")
    assert voices["available"] and len(voices["voices"]) >= 5, voices
    print("voices available:", len(voices["voices"]))

    v1 = call("POST", f"/shots/{shots[0]['id']}/voice",
              {"text": "这天要变了。", "voice": "zh-CN-YunxiNeural", "character_id": hero["id"]})
    v2 = call("POST", f"/shots/{shots[1]['id']}/voice",
              {"text": "你果然在这里。", "voice": "zh-CN-XiaoxiaoNeural",
               "character_id": companion["id"]})
    print("voice1:", v1["audio_url"], f"{v1['duration_s']}s", v1["engine"])
    print("voice2:", v2["audio_url"], f"{v2['duration_s']}s", v2["engine"])
    assert v1["duration_s"] > 0.5 and v2["duration_s"] > 0.5
    for v in (v1, v2):
        p = OUTPUTS / v["audio_url"][len("/outputs/"):]
        assert p.exists() and p.stat().st_size > 1000, p

    print("== 4. 质量检查 + 审核留痕 ==")
    q = call("GET", f"/shots/{shots[0]['id']}/quality")
    print("quality score:", q["score"])
    # 未生成版本 → error（no_version）是正确行为；但不应误报 no_character
    assert any(i["code"] == "no_version" for i in q["issues"]), q
    assert not any(i["code"] == "no_character" for i in q["issues"]), q
    assert not any(i["code"] == "no_voice" for i in q["issues"]), q  # 已生成配音

    # 审核打回（无版本时应 400）
    try:
        call("POST", f"/shots/{shots[0]['id']}/review", {"action": "approve"})
        raise AssertionError("无版本审核通过应该报错")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
        print("approve w/o version correctly rejected:", e.code)

    print("== 5. 造测试视频 → 版本 → 审核通过 → 历史 ==")
    ffmpeg = _get_ffmpeg()
    for i, sh in enumerate(shots):
        seg = OUTPUTS / f"e2e2_{sh['id']}.mp4"
        subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i",
             f"testsrc=size=256x256:rate=8:duration=2.5", "-pix_fmt", "yuv420p",
             str(seg)],
            capture_output=True, check=True,
        )
        ver = _create_version(sh["id"], f"/outputs/e2e2_{sh['id']}.mp4")
        call("POST", f"/shots/{sh['id']}/versions/{ver['id']}/adopt")
    reviewed = call("POST", f"/shots/{shots[0]['id']}/review",
                    {"action": "approve", "comment": "构图和配音都OK"})
    assert reviewed["status"] == "approved"
    assert reviewed["quality"]["score"]["level"] in ("ok", "warn")
    print("reviewed:", reviewed["status"], "| quality:", reviewed["quality"]["score"])
    for sh in shots[1:]:
        call("POST", f"/shots/{sh['id']}/review", {"action": "approve"})

    hist = call("GET", f"/projects/{pid}/history")
    print("history entries:", len(hist))
    assert len(hist) >= 3
    assert hist[0]["action"] == "approve"
    assert "checks" in hist[0] and isinstance(hist[0]["checks"], list)

    print("== 6. 返工（含级联） ==")
    rw = call("POST", f"/shots/{shots[1]['id']}/rework",
              {"cascade": True, "comment": "第二镜情绪不对，连后面一起重做"})
    print("rework reset:", rw["reset"])
    assert shots[1]["id"] in rw["reset"] and shots[2]["id"] in rw["reset"]
    after = call("GET", f"/shots/{shots[1]['id']}")
    assert after["status"] == "draft", after["status"]
    # 重新审核通过（时间线同步需要 approved）
    call("POST", f"/shots/{shots[1]['id']}/review", {"action": "approve"})
    call("POST", f"/shots/{shots[2]['id']}/review", {"action": "approve"})

    print("== 7. 时间线 + 字幕自动生成 ==")
    tl = call("POST", f"/projects/{pid}/timeline/sync")
    print("timeline items:", len(tl))
    assert len(tl) == 3
    subs = call("POST", f"/projects/{pid}/subtitles/auto")
    print("auto subtitles:", len(subs))
    assert len(subs) == 2  # 第三镜无对白
    assert subs[0]["text"] == "林越：这天要变了。" and subs[0]["start_s"] == 0.0
    assert subs[1]["start_s"] > 0
    srt = call("GET", f"/projects/{pid}/subtitles/srt")["srt"]
    assert "00:00:00,000 --> 00:00:02,500" in srt, srt
    print("SRT head:", srt.splitlines()[1])

    # 音乐轨（造一段 3 秒正弦音）
    music = OUTPUTS / "uploads" / "e2e_music.wav"
    music.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         str(music)], capture_output=True, check=True)
    track = call("POST", f"/projects/{pid}/audio-tracks",
                 {"kind": "music", "title": "背景弦乐", "audio_url": "/outputs/uploads/e2e_music.wav",
                  "start_s": 0.0, "volume": 0.5})
    print("music track:", track["id"], track["title"])
    assert track["volume"] == 0.5
    tracks_list = call("GET", f"/projects/{pid}/audio-tracks")
    assert len(tracks_list) == 1

    print("== 8. 导出（混音 + 字幕烧录） ==")
    exp = call("POST", f"/projects/{pid}/export")
    eid = exp["id"]
    import time
    for _ in range(120):
        e = call("GET", f"/exports/{eid}")
        if e["status"] in ("done", "failed"):
            break
        time.sleep(1)
    print("export status:", e["status"], "| stage:", e.get("stage"), "| err:", e.get("error") or "-")
    assert e["status"] == "done", e
    out = OUTPUTS / e["file_url"][len("/outputs/"):]
    assert out.exists() and out.stat().st_size > 10000, out
    dur = _probe_duration(out)
    print(f"exported: {out.name} {out.stat().st_size // 1024}KB duration={dur}s")
    assert 6.5 <= dur <= 9.0, dur  # 3 段 2.5s

    print("== 9. 清理 ==")
    call("DELETE", f"/projects/{pid}")
    for f in OUTPUTS.glob("e2e2_*.mp4"):
        f.unlink(missing_ok=True)
    (OUTPUTS / "uploads" / "e2e_music.wav").unlink(missing_ok=True)
    left = call("GET", "/projects")
    assert not any(p["id"] == pid for p in left)
    print("ALL E2E-2 PASSED")


def _create_version(shot_id: str, video_url: str) -> dict:
    """直接走数据层造版本（避免跑真实生成）"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import storyboard as sb

    job = {"id": f"e2e2_{shot_id}", "prompt": "test", "engine": "anime",
           "width": 256, "height": 256, "num_frames": 20, "fps": 8,
           "steps": 20, "guidance": 7.5, "seed": 42}
    return sb.create_version(shot_id, job, video_url, "")


def _get_ffmpeg() -> str:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.video import get_ffmpeg

    return get_ffmpeg()


def _probe_duration(p: Path) -> float:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.video import probe_duration

    return probe_duration(p)


if __name__ == "__main__":
    main()
