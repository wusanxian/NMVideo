"""导出链路复测：用已有任务视频拼项目 → 同步时间线 → 导出 → 校验时长/分辨率"""
import glob
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from app import storyboard as sb  # noqa: E402
from app.config import OUTPUTS_DIR  # noqa: E402

BASE = "http://127.0.0.1:8000/api"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main():
    # 找两个已有的完成视频
    jobs = []
    import sqlite3
    conn = sqlite3.connect("aivideo.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status='done' AND video_url IS NOT NULL LIMIT 2"
    ).fetchall()
    jobs = [dict(r) for r in rows]
    conn.close()
    assert len(jobs) >= 2, "需要至少 2 个已完成的历史任务"

    _, proj = call("POST", "/projects", {"name": "导出复测", "description": "x"})
    pid = proj["id"]
    _, sc = call("POST", f"/projects/{pid}/scenes", {"title": "测试场景"})
    for i, j in enumerate(jobs[:2]):
        _, shot = call("POST", f"/scenes/{sc['id']}/shots", {"title": f"片段{i + 1}"})
        # 直接落一个指向历史视频的版本
        v = sb.create_version(shot["id"], j, j["video_url"], j.get("poster_url") or "")
        sb.update_shot(shot["id"], current_version_id=v["id"])
        sb.set_shot_status(shot["id"], "approved")

    _, tl = call("POST", f"/projects/{pid}/timeline/sync")
    print("timeline:", len(tl), "| 片段1 trim_out:", tl[0]["trim_out_s"])
    _, exp = call("POST", f"/projects/{pid}/export")
    eid = exp["id"]
    t0 = time.time()
    while time.time() - t0 < 120:
        _, exp = call("GET", f"/exports/{eid}")
        if exp["status"] in ("done", "failed"):
            break
        time.sleep(2)
    print("export:", exp["status"], exp.get("error") or "")
    assert exp["status"] == "done", exp

    from app.video import probe_duration, probe_resolution
    out = OUTPUTS_DIR / "exports" / f"{eid}.mp4"
    dur = probe_duration(out)
    w, h = probe_resolution(out)
    print(f"导出文件: {out.stat().st_size / 1024:.0f} KB | {w}x{h} | {dur:.2f}s")
    assert dur > 0, "导出时长为 0"
    assert w > 0 and h > 0, "分辨率探测失败"

    # 清理
    call("DELETE", f"/projects/{pid}")
    out.unlink(missing_ok=True)
    print("\nEXPORT TEST PASS ✓")


if __name__ == "__main__":
    main()
