# -*- coding: utf-8 -*-
"""Publish release/<ver>/ assets to GitHub Releases (NMVideo).

Usage: python scripts/publish_release.py <version>
Env:   GH_RELEASE_TOKEN must be set.
"""
import http.client
import json
import os
import sys
import time
from pathlib import Path

REPO = "wusanxian/NMVideo"
ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("GH_RELEASE_TOKEN", "")

# ASCII-only asset names (GitHub silently mangles non-ASCII names)
ASSETS = [
    "aivideo-desktop-setup-{v}.exe",
    "aivideo-desktop-setup-{v}.exe.blockmap",
    "latest.yml",
    "NMVideo.exe",
    "INSTALL.md",
    "SHA256SUMS.txt",
    "BUILD-INFO.json",
]


def req(method, path, body=None, headers=None, _retry=3):
    for attempt in range(1, _retry + 1):
        try:
            conn = http.client.HTTPSConnection("api.github.com", timeout=120)
            h = {"User-Agent": "nmvideo-release", "Accept": "application/vnd.github+json"}
            if TOKEN:
                h["Authorization"] = f"Bearer {TOKEN}"
            if headers:
                h.update(headers)
            conn.request(method, path, body=body, headers=h)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            return resp.status, data
        except (OSError, http.client.HTTPException) as e:
            print(f"[!] network error ({method} {path}) attempt {attempt}: {e!r}")
            if attempt == _retry:
                raise
            time.sleep(20)


def main():
    version = sys.argv[1]
    rel_dir = ROOT / "release" / version
    if not rel_dir.is_dir():
        print(f"[!] missing dir: {rel_dir}")
        sys.exit(1)
    tag = f"v{version}"

    # ---- create release (or reuse existing draft/release) ----
    status, data = req("POST", f"/repos/{REPO}/releases", body=json.dumps({
        "tag_name": tag,
        "target_commitish": os.environ.get("RELEASE_TARGET_SHA", ""),
        "name": f"NMVideo {version}",
        "body": (rel_dir / "BODY.md").read_text(encoding="utf-8") if (rel_dir / "BODY.md").exists()
                else f"NMVideo {version}",
        "draft": False,
        "prerelease": False,
    }).encode("utf-8"), headers={"Content-Type": "application/json"})
    if status == 201:
        release = json.loads(data)
        print(f"[OK] release created id={release['id']} tag={tag}")
    elif status == 422:
        # already exists -> fetch it
        status, data = req("GET", f"/repos/{REPO}/releases/tags/{tag}")
        if status != 200:
            print(f"[!] cannot fetch existing release: {status} {data[:300]!r}")
            sys.exit(1)
        release = json.loads(data)
        print(f"[OK] release exists id={release['id']}, will update assets")
        # update body/name
        req("PATCH", f"/repos/{REPO}/releases/{release['id']}", body=json.dumps({
            "name": f"NMVideo {version}",
            "body": (rel_dir / "BODY.md").read_text(encoding="utf-8") if (rel_dir / "BODY.md").exists()
                    else f"NMVideo {version}",
        }).encode("utf-8"), headers={"Content-Type": "application/json"})
    else:
        print(f"[!] create release failed: {status} {data[:400]!r}")
        sys.exit(1)

    release_id = release["id"]

    # ---- existing assets map (for 422 delete+reupload) ----
    status, data = req("GET", f"/repos/{REPO}/releases/{release_id}/assets?per_page=100")
    existing = {a["name"]: a["id"] for a in json.loads(data)} if status == 200 else {}
    print(f"[OK] existing assets: {list(existing) or 'none'}")

    for pattern in ASSETS:
        name = pattern.format(v=version)
        path = rel_dir / name
        if not path.is_file():
            print(f"[!] SKIP missing asset file: {name}")
            continue
        size = path.stat().st_size
        if name in existing:
            st, _ = req("DELETE", f"/repos/{REPO}/releases/assets/{existing[name]}")
            print(f"[OK] deleted old asset {name} (status {st})")
        # uploads.github.com, stream via chunked file body
        url = f"/repos/{REPO}/releases/{release_id}/assets?name={name}"
        ok = False
        for attempt in range(1, 6):
            try:
                conn = http.client.HTTPSConnection("uploads.github.com", timeout=1800)
                conn.request("POST", url, body=path.open("rb"),
                             headers={
                                 "User-Agent": "nmvideo-release",
                                 "Authorization": f"Bearer {TOKEN}",
                                 "Content-Type": "application/octet-stream",
                                 "Content-Length": str(size),
                             })
                resp = conn.getresponse()
                resp.read()
                conn.close()
                if resp.status == 201:
                    print(f"[OK] uploaded {name} ({size} B)")
                    ok = True
                    break
                print(f"[!] upload {name} status {resp.status} (attempt {attempt}), body: {resp.read()[:200] if False else ''}")
                if resp.status == 422:
                    # fetch asset id, delete, retry
                    st, data = req("GET", f"/repos/{REPO}/releases/{release_id}/assets?per_page=100")
                    for a in json.loads(data):
                        if a["name"] == name:
                            req("DELETE", f"/repos/{REPO}/releases/assets/{a['id']}")
                            print(f"[OK] deleted conflicting asset {name}")
                            break
                time.sleep(15)
            except (OSError, http.client.HTTPException) as e:
                print(f"[!] upload {name} error attempt {attempt}: {e!r}")
                time.sleep(25)
        if not ok:
            print(f"[X] FAILED to upload {name}")
            sys.exit(2)

    # ---- final verify ----
    status, data = req("GET", f"/repos/{REPO}/releases/{release_id}")
    rel = json.loads(data)
    print(f"[OK] release url: {rel['html_url']}")
    for a in rel["assets"]:
        print(f"     asset {a['name']} {a['size']} B state={a['state']}")
    # tag must point to current head
    status, data = req("GET", f"/repos/{REPO}/git/ref/tags/{tag}")
    if status == 200:
        obj = json.loads(data)["object"]
        print(f"[OK] tag {tag} -> {obj['sha'][:8]} ({obj['type']})")
    print("[DONE]")


if __name__ == "__main__":
    main()
