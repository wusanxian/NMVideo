"""PR-02（图中 PR-02 / 改造.md PR-08）：独立首帧 + 尾帧 + 续接来源版本。

验收对照（改造.md PR-08）：
- 首尾两张不同图片分别到达正确节点（不同 LoadImage 节点）
- 工作流能力不支持（模板未映射尾帧节点）时明确提示，不默默忽略
- 旧 reference_image 兼容迁移：i2v 行为不变；first_last 旧单图充当首帧
- 续接记录来源版本；上游变更后下游标记「继承帧已过期」
"""
import json
from pathlib import Path

import pytest

GOOD_PROMPT = "武侠对决，两名剑客面对面站位，收招定格（中景, 固定, 4秒）"


def _mk_project_shot(client, name):
    pid = client.post("/api/projects",
                      json={"name": name, "description": ""}).json()["id"]
    client.post(f"/api/projects/{pid}/script/import",
                json={"text": "场景一：雨夜\n甲走在街上（远景, 固定, 4秒）",
                      "mode": "rebuild"})
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    return pid, sid


def _patch_template(monkeypatch, first_node=None, last_node=None, ref_node=None):
    """模板：按需把首/尾帧/参考图映射到不同 LoadImage 节点。"""
    import app.comfyui as comfyui_mod

    nodes = {"9": {"class_type": "LoadImage", "inputs": {"image": ""}},
             "10": {"class_type": "LoadImage", "inputs": {"image": ""}}}
    mapping = {}
    if ref_node:
        mapping["reference_image"] = {"node": ref_node, "input": "image"}
    if first_node:
        mapping["first_frame_image"] = {"node": first_node, "input": "image"}
    if last_node:
        mapping["last_frame_image"] = {"node": last_node, "input": "image"}
    monkeypatch.setattr(
        comfyui_mod, "load_workflow_template",
        lambda: {"prompt": nodes, "_aivideo_map": mapping},
    )


def _patch_uploads(monkeypatch):
    """上传按 URL 返回不同 ComfyUI input 引用名（可断言「分别到达正确节点」）。"""
    import app.comfyui as comfyui_mod

    table = {
        "/outputs/first.png": "first_uploaded.png",
        "/outputs/last.png": "last_uploaded.png",
    }

    def fake_upload(c, url):
        return table.get(url, f"{url}_uploaded")

    monkeypatch.setattr(comfyui_mod, "upload_reference_image", fake_upload)


def _patch_comfy_available(monkeypatch):
    from app.comfyui import ComfyUIClient

    monkeypatch.setattr(ComfyUIClient, "is_available",
                        lambda self, timeout=2.5: True)


# =====================================================================
# 首尾帧：两张不同图片分别到达正确节点
# =====================================================================
def test_first_last_two_images_reach_two_nodes(client, fresh_db, tmp_path,
                                               monkeypatch):
    """PR-02 验收：first_last 的首/尾帧分别注入两个不同 LoadImage 节点。"""
    import app.routers.storyboard as sb_router

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    _patch_comfy_available(monkeypatch)
    _patch_template(monkeypatch, first_node="9", last_node="10")
    _patch_uploads(monkeypatch)

    pid, sid = _mk_project_shot(client, "fl-two-nodes")
    r_patch = client.patch(f"/api/shots/{sid}", json={
        "first_frame_image": "/outputs/first.png",
        "last_frame_image": "/outputs/last.png",
    })
    assert r_patch.status_code == 200, r_patch.text
    assert r_patch.json()["first_frame_image"] == "/outputs/first.png"
    assert r_patch.json()["last_frame_image"] == "/outputs/last.png"

    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "first_last"})
    assert r.status_code == 201, r.text
    job = r.json()
    # 入队冻结：job 记录的是本机 URL（可复现），不是 ComfyUI 引用名
    assert job["first_frame_image"] == "/outputs/first.png"
    assert job["last_frame_image"] == "/outputs/last.png"
    # 快照里两张图分别到达各自节点，且互不相同
    snap = Path(job["workflow_snapshot"])
    payload = json.loads(snap.read_text(encoding="utf-8"))
    graph = payload["graph"]
    assert graph["9"]["inputs"]["image"] == "first_uploaded.png"
    assert graph["10"]["inputs"]["image"] == "last_uploaded.png"
    assert graph["9"]["inputs"]["image"] != graph["10"]["inputs"]["image"]


def test_first_last_requires_last_frame(client, fresh_db, monkeypatch):
    """first_last 只有首帧（旧单图数据）→ 400 明确要求尾帧，不留任务。"""
    _patch_comfy_available(monkeypatch)
    _patch_template(monkeypatch, first_node="9", last_node="10")
    pid, sid = _mk_project_shot(client, "fl-no-last")
    client.patch(f"/api/shots/{sid}",
                 json={"reference_image": "/outputs/first.png"})
    before = len(fresh_db.list_jobs(limit=1000))
    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "first_last"})
    assert r.status_code == 400
    assert "尾帧" in r.json()["detail"]
    assert len(fresh_db.list_jobs(limit=1000)) == before, "失败后仍创建了 job"


# =====================================================================
# 工作流不支持时明确提示（不默默忽略）
# =====================================================================
def test_first_last_unmapped_last_frame_rejected(client, fresh_db, monkeypatch):
    """模板未映射尾帧节点 → 400 明示，不静默丢图。"""
    _patch_comfy_available(monkeypatch)
    _patch_template(monkeypatch, first_node="9")  # 无 last 映射
    pid, sid = _mk_project_shot(client, "fl-unmapped")
    client.patch(f"/api/shots/{sid}", json={
        "first_frame_image": "/outputs/first.png",
        "last_frame_image": "/outputs/last.png",
    })
    before = len(fresh_db.list_jobs(limit=1000))
    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "first_last"})
    assert r.status_code == 400
    assert "尾帧节点" in r.json()["detail"]
    assert len(fresh_db.list_jobs(limit=1000)) == before


def test_first_last_unmapped_first_frame_rejected(client, fresh_db, monkeypatch):
    """模板只有尾帧映射、首帧无任何可走节点 → 400 明示。"""
    _patch_comfy_available(monkeypatch)
    _patch_template(monkeypatch, last_node="10")  # 无 first / reference 映射
    pid, sid = _mk_project_shot(client, "fl-unmapped-first")
    client.patch(f"/api/shots/{sid}", json={
        "first_frame_image": "/outputs/first.png",
        "last_frame_image": "/outputs/last.png",
    })
    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "first_last"})
    assert r.status_code == 400
    assert "首帧节点" in r.json()["detail"]


# =====================================================================
# 旧 reference_image 兼容
# =====================================================================
def test_legacy_first_last_ref_as_first_frame(client, fresh_db, tmp_path,
                                              monkeypatch):
    """旧式混合模板（reference_image + last_frame_image）+ 旧数据只有单参考图：
    reference_image 充当首帧走参考图节点，尾帧走尾帧节点。"""
    import app.routers.storyboard as sb_router

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    _patch_comfy_available(monkeypatch)
    _patch_template(monkeypatch, last_node="10", ref_node="9")
    _patch_uploads(monkeypatch)

    pid, sid = _mk_project_shot(client, "fl-legacy")
    client.patch(f"/api/shots/{sid}", json={
        "reference_image": "/outputs/first.png",
        "last_frame_image": "/outputs/last.png",
    })
    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "first_last"})
    assert r.status_code == 201, r.text
    graph = json.loads(
        Path(r.json()["workflow_snapshot"]).read_text(encoding="utf-8"))["graph"]
    # 首帧经 reference_image 节点（旧式模板），尾帧经专用节点
    assert graph["9"]["inputs"]["image"] == "first_uploaded.png"
    assert graph["10"]["inputs"]["image"] == "last_uploaded.png"


def test_i2v_behavior_unchanged(client, fresh_db, tmp_path, monkeypatch):
    """回归护栏：i2v 仍走 reference_image 单图链路，不注入首/尾帧专用节点。"""
    import app.routers.storyboard as sb_router

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    _patch_comfy_available(monkeypatch)
    _patch_template(monkeypatch, ref_node="9")
    _patch_uploads(monkeypatch)

    pid, sid = _mk_project_shot(client, "i2v-unchanged")
    client.patch(f"/api/shots/{sid}",
                 json={"reference_image": "/outputs/first.png"})
    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "i2v"})
    assert r.status_code == 201, r.text
    graph = json.loads(
        Path(r.json()["workflow_snapshot"]).read_text(encoding="utf-8"))["graph"]
    assert graph["9"]["inputs"]["image"] == "first_uploaded.png"
    assert graph["10"]["inputs"]["image"] == "", "未映射的节点不应被注入"


# =====================================================================
# 续接：来源版本记录 + 下游继承帧过期提示
# =====================================================================
def _mk_source_version(fresh_db, tmp_path, src_shot):
    """给来源镜头造一个采用版本（含真实存在的视频文件），返回 version_id。"""
    import app.storyboard as sb

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    video = out / "src.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")  # 受管后缀 + 非空即可
    ver = sb.create_version(src_shot["id"],
                            job={"id": "jobsrc", "prompt": "x", "engine": "anime"},
                            video_url="/outputs/src.mp4")
    sb.update_shot(src_shot["id"], current_version_id=ver["id"])
    return ver["id"]


def test_continuation_records_source_version(client, fresh_db, tmp_path,
                                             monkeypatch):
    """continuation 生成后：shot 记录 source_video_version_id（可复现）。"""
    import app.routers.storyboard as sb_router
    import app.storyboard as sb
    import app.video as video_mod

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)

    pid, sid = _mk_project_shot(client, "cont-src")
    scene_id = sb.get_shot(sid)["scene_id"]
    dst = sb.create_shot(scene_id, title="续接镜头")
    client.patch(f"/api/shots/{dst['id']}", json={"prompt": GOOD_PROMPT})
    src_ver_id = _mk_source_version(fresh_db, tmp_path, sb.get_shot(sid))
    _patch_comfy_available(monkeypatch)

    # 抽帧不必真跑 ffmpeg：替换为写假 PNG
    def fake_extract(src_path, frame_path):
        Path(frame_path).write_bytes(b"\x89PNG fake")

    monkeypatch.setattr(video_mod, "extract_last_frame", fake_extract)

    r = client.post(f"/api/shots/{dst['id']}/generate",
                    json={"engine": "comfyui", "mode": "continuation"})
    assert r.status_code == 201, r.text
    shot = client.get(f"/api/shots/{dst['id']}").json()
    assert shot["source_video_version_id"] == src_ver_id
    assert shot["inheritance_stale"] is False, "来源版本未变，不应标记过期"
    assert shot["reference_image"].startswith("/outputs/refs/")


def test_inheritance_stale_after_source_changes(client, fresh_db, tmp_path,
                                                monkeypatch):
    """上游重生成了新版本 → 下游续接镜头标记 inheritance_stale=True。"""
    import app.routers.storyboard as sb_router
    import app.storyboard as sb
    import app.video as video_mod

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    # adopt_version 的视频文件校验走默认根（动态读 app.config.OUTPUTS_DIR）
    import app.config as config_mod

    monkeypatch.setattr(config_mod, "OUTPUTS_DIR", out)

    pid, sid = _mk_project_shot(client, "stale-src")
    scene_id = sb.get_shot(sid)["scene_id"]
    dst = sb.create_shot(scene_id, title="续接镜头")
    client.patch(f"/api/shots/{dst['id']}", json={"prompt": GOOD_PROMPT})
    src_ver_id = _mk_source_version(fresh_db, tmp_path, sb.get_shot(sid))
    sb.update_shot(dst["id"], mode="continuation", continue_from=sid,
                   source_video_version_id=src_ver_id)
    _patch_comfy_available(monkeypatch)

    def fake_extract(src_path, frame_path):
        Path(frame_path).write_bytes(b"\x89PNG fake")

    monkeypatch.setattr(video_mod, "extract_last_frame", fake_extract)
    r = client.post(f"/api/shots/{dst['id']}/generate",
                    json={"engine": "comfyui", "mode": "continuation"})
    assert r.status_code == 201, r.text

    # 上游重新生成：换一个新版本并采用 → 下游继承帧过期
    newer = sb.create_version(sid, job={"id": "jobsrc2", "prompt": "x",
                                        "engine": "anime"},
                              video_url="/outputs/src.mp4")
    sb.adopt_version(sid, newer["id"])
    shot = client.get(f"/api/shots/{dst['id']}").json()
    assert shot["inheritance_stale"] is True, "上游已换版本，应提示继承帧过期"
    assert shot["source_video_version_id"] == src_ver_id


def test_engine_fallback_uploads_first_last(tmp_path, monkeypatch):
    """引擎兜底路径（无快照）也应上传首/尾帧——映射了才传。"""
    from app.engines.comfy import ComfyEngine

    import app.comfyui as comfyui_mod
    import app.engines.comfy as comfy_engine_mod

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(comfy_engine_mod, "OUTPUTS_DIR", out)
    # 注意：engines/comfy.py 是模块顶直接名导入（from ..comfyui import ...），
    # 引擎内部调用走本模块命名空间——必须 patch comfy_engine_mod 上的绑定
    monkeypatch.setattr(
        comfy_engine_mod, "load_workflow_template",
        lambda: {"prompt": {"9": {"class_type": "LoadImage",
                                  "inputs": {"image": ""}},
                            "10": {"class_type": "LoadImage",
                                   "inputs": {"image": ""}}},
                 "_aivideo_map": {"first_frame_image": {"node": "9", "input": "image"},
                                  "last_frame_image": {"node": "10", "input": "image"}}},
    )
    uploaded = []
    monkeypatch.setattr(comfy_engine_mod, "upload_reference_image",
                        lambda c, url: uploaded.append(url) or f"{url}_up")

    engine = ComfyEngine()
    graph = engine._resolve_workflow(
        "", {"prompt": "x", "first_frame_image": "/outputs/first.png",
             "last_frame_image": "/outputs/last.png"},
        tmp_path / "jobFL" / "frames")
    assert graph["9"]["inputs"]["image"] == "/outputs/first.png_up"
    assert graph["10"]["inputs"]["image"] == "/outputs/last.png_up"
    assert sorted(uploaded) == ["/outputs/first.png", "/outputs/last.png"]
