"""分镜工作台端到端冒烟测试：项目 → 剧本拆分 → 生成 → 审核 → 时间线 → 导出

用法：先启动后端，然后
    ./.venv/Scripts/python.exe scripts/e2e_storyboard.py
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api"
TEST_SCRIPT = """场景一：雨夜街道
主角独自走在霓虹灯下的长街，雨水打湿肩头 (全景, 移, 4秒)
主角：这天要变了。

场景二：密室
烛光摇曳，主角推开木门 (远景, 推, 3秒)
"""


def call(method: str, path: str, body: dict = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main():
    print("== 1. 创建项目 ==")
    _, proj = call("POST", "/projects", {"name": "E2E 测试", "description": "自动测试"})
    pid = proj["id"]
    print("project:", pid)

    print("== 2. AI 拆分剧本 ==")
    _, r = call("POST", f"/projects/{pid}/script/import", {"text": TEST_SCRIPT})
    print(f"scenes={r['scene_count']} shots={r['shot_count']}")
    assert r["scene_count"] == 2 and r["shot_count"] == 2, r

    print("== 3. 项目结构 ==")
    _, full = call("GET", f"/projects/{pid}")
    shot = full["scenes"][0]["shots"][0]
    print("scene1:", full["scenes"][0]["title"], "| shot1:", shot["title"],
          "|", shot["shot_size"], shot["camera_movement"], shot["duration_s"], "s")
    assert shot["shot_size"] == "全景" and shot["camera_movement"] == "移镜", shot
    assert shot["dialogue"] == "主角：这天要变了。", shot

    print("== 4. 填提示词并提交生成 ==")
    _, shot = call("PATCH", f"/shots/{shot['id']}", {
        "prompt": "1girl standing in rainy neon street, anime style, night, wet ground reflection",
        "negative_prompt": "worst quality, blurry",
    })
    _, job = call("POST", f"/shots/{shot['id']}/generate", {
        "engine": "anime", "width": 384, "height": 384,
        "num_frames": 8, "fps": 8, "steps": 10, "guidance": 7.5, "seed": 42,
    })
    jid = job["id"]
    print("job:", jid, "shot status ->", "queued")

    print("== 5. 等待生成完成 ==")
    t0 = time.time()
    while time.time() - t0 < 600:
        _, job = call("GET", f"/jobs/{jid}")
        if job["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(3)
    print("job status:", job["status"], f"({time.time() - t0:.0f}s)",
          job.get("error") or "")
    assert job["status"] == "done", job

    print("== 6. 分镜状态与版本 ==")
    _, shot = call("GET", f"/shots/{shot['id']}")
    versions = call("GET", f"/shots/{shot['id']}/versions")[1]
    print("shot status:", shot["status"], "| versions:", len(versions),
          "| current:", shot["current_version_id"] == versions[0]["id"])
    assert shot["status"] == "review" and len(versions) == 1, shot
    assert versions[0]["video_url"] and versions[0]["seed"] == 42, versions[0]

    print("== 7. 审核通过 ==")
    _, shot = call("POST", f"/shots/{shot['id']}/review", {"action": "approve"})
    assert shot["status"] == "approved", shot
    print("shot status ->", shot["status"])

    print("== 8. 同步时间线并导出 ==")
    _, tl = call("POST", f"/projects/{pid}/timeline/sync")
    print("timeline items:", len(tl))
    assert len(tl) == 1, tl
    _, exp = call("POST", f"/projects/{pid}/export")
    eid = exp["id"]
    t0 = time.time()
    while time.time() - t0 < 180:
        _, exp = call("GET", f"/exports/{eid}")
        if exp["status"] in ("done", "failed"):
            break
        time.sleep(2)
    print("export:", exp["status"], exp.get("error") or "", f"({time.time() - t0:.0f}s)")
    assert exp["status"] == "done", exp

    print("== 9. 清理 ==")
    call("DELETE", f"/jobs/{jid}")
    call("DELETE", f"/projects/{pid}")
    print("\nE2E PASS ✓  全部链路通")


if __name__ == "__main__":
    sys.exit(main())
