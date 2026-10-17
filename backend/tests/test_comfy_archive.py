"""2C 输入上传与产物原样归档测试：参考图上传 ComfyUI input 目录 / 原生视频
免抽帧重编码（remux 原样归档）/ 队列直通分支 / 帧序列路径不回退。

覆盖审查项 2C：
- 参考图（i2v/首尾帧）经 /upload/image 进 ComfyUI input 目录，工作流注入
  返回名（"subfolder/name"）而不是本机路径
- 入队时（router）上传并写进快照；执行时无快照才兜底上传（不重复上传）
- ComfyUI 原生视频输出：引擎设 last_native_video 并返回空帧列表，
  队列 remux（-c copy 码流原样 + comment 元数据）+ 抽海报帧 + 清临时文件
- remux 失败兜底原样拷贝；非 mp4 容器走转码分支；本地引擎/图片序列
  输出仍走帧序列合成（行为不变）
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import queue_manager as qm
from app import storyboard as sb
from app.comfyui import (
    ComfyUICancelled,
    ComfyUIClient,
    ComfyUIError,
    local_path_from_output_url,
    upload_reference_image,
)
from app.engines.comfy import ComfyEngine
from app.video import (
    extract_first_frame,
    probe_resolution,
    read_video_metadata,
    remux_video,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x18ftypmp42" * 8
GOOD_PROMPT = "雨夜街头，霓虹灯下的长镜头，湿地反光"

_COMPLETED_ENTRY = {
    "status": {"completed": True, "status_str": "success"},
    "outputs": {"7": {"images": [
        {"filename": "aivideo_00001.png", "subfolder": "", "type": "output"},
    ]}},
}

_NATIVE_ENTRY = {
    "status": {"completed": True, "status_str": "success"},
    "outputs": {"9": {"videos": [
        {"filename": "aivideo_00001.mp4", "subfolder": "", "type": "output"},
    ]}},
}


@pytest.fixture(autouse=True)
def _clean_queue():
    """job_queue 是进程级单例：清理队列与引擎缓存，避免跨测试残留。"""
    yield
    while not qm.job_queue._queue.empty():
        try:
            qm.job_queue._queue.get_nowait()
        except Exception:
            break
    qm.job_queue._engine_instances.clear()


def _tiny_mp4(path: Path) -> Path:
    """用 imageio-ffmpeg 静态 ffmpeg 造一个真实可解码的小视频（remux 测试用）。"""
    from app.video import get_ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [get_ffmpeg(), "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.5",
         "-r", "8", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"测试视频生成失败: {proc.stderr[-300:]}"
    return path


class _Resp:
    def __init__(self, data, code=200):
        self._data, self.status_code = data, code

    def json(self):
        return self._data

    @property
    def text(self):
        return json.dumps(self._data)


class _RecordingRequests:
    """假 requests：记录 get/post 调用，按 payload 返回。"""

    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        return _Resp(self.payload, self.code)

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        return _Resp(self.payload, self.code)


class _FakeClient:
    """记录调用的假 ComfyUI 客户端（含 upload_image）。"""

    def __init__(self, entry, download_bytes=PNG_BYTES):
        self.entry = entry
        self.download_bytes = download_bytes
        self.calls = {}
        self.uploaded = []

    def is_available(self, timeout=2.5):
        return True

    def new_client_id(self, prefix="aivideo"):
        return "aivideo-fixedcid"

    def submit(self, graph, client_id="aivideo"):
        self.calls["submit"] = {"client_id": client_id, "graph": graph}
        return "pid_fake"

    def wait_for_completion(self, prompt_id, on_progress=None, is_cancelled=None,
                            timeout_s=3600, client_id=""):
        self.calls["wait"] = {"prompt_id": prompt_id, "client_id": client_id}
        return self.entry

    def download(self, filename, subfolder="", ftype="output"):
        self.calls["download"] = filename
        return self.download_bytes

    def upload_image(self, path, name=""):
        self.uploaded.append(str(path))
        return {"name": Path(path).name, "subfolder": "clipspace", "type": "input"}


def _mk_engine(tmp_path, monkeypatch, entry, download_bytes=PNG_BYTES):
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.engines.comfy.OUTPUTS_DIR", out)
    monkeypatch.setattr("app.comfyui.OUTPUTS_DIR", out)
    engine = ComfyEngine()
    engine.client = _FakeClient(entry, download_bytes)
    return engine, out


def _gen_kwargs(frames_dir):
    return dict(
        prompt=GOOD_PROMPT, negative_prompt="", width=512, height=512,
        num_frames=16, fps=8, steps=25, guidance=7.5, seed=7,
        frames_dir=frames_dir, on_progress=lambda p, s: None,
        is_cancelled=lambda: False,
    )


# =====================================================================
# 客户端层：upload_image / URL 解析 / upload_reference_image
# =====================================================================
def test_upload_image_posts_multipart(monkeypatch, tmp_path):
    """upload_image：POST /upload/image multipart，overwrite=true，解析 name。"""
    import app.comfyui as comfyui_mod

    rec = _RecordingRequests({"name": "ref.png", "subfolder": "", "type": "input"})
    monkeypatch.setattr(comfyui_mod, "requests", rec)
    img = tmp_path / "ref.png"
    img.write_bytes(PNG_BYTES)

    data = ComfyUIClient("http://127.0.0.1:8188").upload_image(img)
    assert data["name"] == "ref.png" and data["type"] == "input"
    method, url, kw = rec.calls[-1]
    assert method == "post" and url.endswith("/upload/image")
    assert kw["data"]["overwrite"] == "true", "未要求覆盖同名输入文件"
    assert "image" in kw["files"], "multipart 字段名应为 image"


def test_upload_image_non_200_raises(monkeypatch, tmp_path):
    import app.comfyui as comfyui_mod

    rec = _RecordingRequests({"error": "boom"}, code=500)
    monkeypatch.setattr(comfyui_mod, "requests", rec)
    img = tmp_path / "ref.png"
    img.write_bytes(PNG_BYTES)
    with pytest.raises(ComfyUIError):
        ComfyUIClient("http://127.0.0.1:8188").upload_image(img)


def test_local_path_from_output_url(monkeypatch, tmp_path):
    """/outputs/... URL → 本机路径；外链/空值 → None。"""
    import app.comfyui as comfyui_mod

    base = tmp_path / "out"
    monkeypatch.setattr(comfyui_mod, "OUTPUTS_DIR", base)
    assert local_path_from_output_url("/outputs/uploads/a.png") == base / "uploads" / "a.png"
    assert local_path_from_output_url(
        "http://127.0.0.1:8000/outputs/refs/b.png") == base / "refs" / "b.png"
    assert local_path_from_output_url("https://cdn.example/x.png") is None
    assert local_path_from_output_url("") is None
    assert local_path_from_output_url("/outputs/") is None
    assert local_path_from_output_url("refs/c.png") is None


def test_upload_reference_image_joins_subfolder(monkeypatch, tmp_path):
    """有 subfolder 时引用名为 "subfolder/name"（LoadImage 约定）。"""
    import app.comfyui as comfyui_mod

    out = tmp_path / "out"
    (out / "uploads").mkdir(parents=True)
    (out / "uploads" / "ref.png").write_bytes(PNG_BYTES)
    monkeypatch.setattr(comfyui_mod, "OUTPUTS_DIR", out)

    class _C:
        def upload_image(self, path, name=""):
            return {"name": "ref.png", "subfolder": "clipspace", "type": "input"}

    assert upload_reference_image(_C(), "/outputs/uploads/ref.png") == "clipspace/ref.png"


def test_upload_reference_image_missing_file_raises(monkeypatch, tmp_path):
    import app.comfyui as comfyui_mod

    monkeypatch.setattr(comfyui_mod, "OUTPUTS_DIR", tmp_path / "out")
    with pytest.raises(ComfyUIError):
        upload_reference_image(None, "/outputs/uploads/none.png")


def test_upload_reference_image_external_url_raises(monkeypatch, tmp_path):
    """外链图片本机无副本，无法上传——明确失败而不是静默丢弃。"""
    import app.comfyui as comfyui_mod

    monkeypatch.setattr(comfyui_mod, "OUTPUTS_DIR", tmp_path / "out")
    with pytest.raises(ComfyUIError):
        upload_reference_image(None, "https://cdn.example/x.png")


# =====================================================================
# 引擎层：原生视频直通 / 图片序列照旧 / 兜底上传 / 快照不重复上传
# =====================================================================
def test_engine_native_video_passthrough(tmp_path, monkeypatch):
    """原生视频输出：下载后设 last_native_video、返回空帧列表、不抽帧。"""
    engine, out = _mk_engine(tmp_path, monkeypatch, _NATIVE_ENTRY,
                             download_bytes=b"FAKE-VIDEO-BYTES")
    frames_dir = tmp_path / "jobN" / "frames"
    result = engine.generate(**_gen_kwargs(frames_dir))

    assert result == [], "原生视频应返回空帧列表（通知队列走 remux 直通）"
    native = Path(engine.last_native_video)
    assert native.is_file() and native.read_bytes() == b"FAKE-VIDEO-BYTES"
    assert native.parent == out, "临时视频应落在 OUTPUTS_DIR"
    assert not list(frames_dir.glob("frame_*.png")), "原生视频不应再抽帧"
    assert engine.last_prompt_id == "pid_fake"


def test_engine_images_still_extract_frames(tmp_path, monkeypatch):
    """图片序列输出：行为不变——逐帧下载、last_native_video 为空。"""
    engine, out = _mk_engine(tmp_path, monkeypatch, _COMPLETED_ENTRY)
    frames_dir = tmp_path / "jobI" / "frames"
    result = engine.generate(**_gen_kwargs(frames_dir))

    assert result and result[0].name == "frame_0000.png"
    assert result[0].read_bytes() == PNG_BYTES
    assert engine.last_native_video == ""


def test_engine_fallback_uploads_reference_and_injects_name(tmp_path, monkeypatch):
    """无快照兜底：模板映射了 reference_image 才上传，注入 input 目录引用名。"""
    engine, out = _mk_engine(tmp_path, monkeypatch, _COMPLETED_ENTRY)
    (out / "uploads").mkdir(parents=True, exist_ok=True)
    (out / "uploads" / "ref.png").write_bytes(PNG_BYTES)
    monkeypatch.setattr("app.engines.comfy.load_workflow_template", lambda: {
        "prompt": {"2": {"inputs": {"text": ""}},
                   "10": {"class_type": "LoadImage", "inputs": {"image": ""}}},
        "_aivideo_map": {"prompt": {"node": "2", "input": "text"},
                         "reference_image": {"node": "10", "input": "image"}},
    })

    graph = engine._resolve_workflow(
        "", {"prompt": "x", "reference_image": "/outputs/uploads/ref.png"},
        tmp_path / "jobR" / "frames")
    assert graph["10"]["inputs"]["image"] == "clipspace/ref.png", "未注入 input 引用名"
    assert engine.client.uploaded, "兜底路径未上传参考图"
    # 兜底也补写快照（2B 契约）
    snap = out / "workflows" / "jobR.json"
    assert snap.is_file() and json.loads(snap.read_text(encoding="utf-8"))[
        "graph"]["10"]["inputs"]["image"] == "clipspace/ref.png"


def test_engine_snapshot_skips_upload(tmp_path, monkeypatch):
    """有快照：直接用快照里的引用名，不重复上传（入队时已上传）。"""
    engine, out = _mk_engine(tmp_path, monkeypatch, _COMPLETED_ENTRY)

    def _boom(*a, **k):
        raise AssertionError("快照路径不应上传参考图")

    monkeypatch.setattr("app.engines.comfy.upload_reference_image", _boom)
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(
        {"job_id": "jobS", "graph": {"10": {"inputs": {"image": "clipspace/old.png"}}}},
    ), encoding="utf-8")

    graph = engine._resolve_workflow(
        str(snap), {"reference_image": "/outputs/uploads/ref.png"},
        tmp_path / "jobS" / "frames")
    assert graph["10"]["inputs"]["image"] == "clipspace/old.png"


def test_engine_fallback_upload_failure_raises(tmp_path, monkeypatch):
    """工作流需要参考图但文件缺失：明确失败，不提交必然报错的工作流。"""
    engine, out = _mk_engine(tmp_path, monkeypatch, _COMPLETED_ENTRY)
    monkeypatch.setattr("app.engines.comfy.load_workflow_template", lambda: {
        "prompt": {"10": {"inputs": {"image": ""}}},
        "_aivideo_map": {"reference_image": {"node": "10", "input": "image"}},
    })
    with pytest.raises(ComfyUIError):
        engine._resolve_workflow(
            "", {"reference_image": "/outputs/uploads/missing.png"},
            tmp_path / "jobM" / "frames")


def test_engine_default_template_ignores_reference(tmp_path, monkeypatch):
    """默认模板不映射 reference_image：纯文生视频不上传（行为兼容）。"""
    engine, out = _mk_engine(tmp_path, monkeypatch, _COMPLETED_ENTRY)

    def _boom(*a, **k):
        raise AssertionError("默认模板不应触发上传")

    monkeypatch.setattr("app.engines.comfy.upload_reference_image", _boom)
    monkeypatch.setattr("app.engines.comfy.load_workflow_template",
                        lambda: {"prompt": {"2": {"inputs": {"text": ""}}},
                                 "_aivideo_map": {"prompt": {"node": "2", "input": "text"}}})
    graph = engine._resolve_workflow(
        "", {"prompt": "x", "reference_image": "/outputs/uploads/ref.png"},
        tmp_path / "jobD" / "frames")
    assert graph["2"]["inputs"]["text"] == "x"


# =====================================================================
# video.py：remux 原样归档 / 非 mp4 转码 / 海报帧（真实 ffmpeg）
# =====================================================================
def test_remux_video_copy_preserves_stream_and_metadata(tmp_path):
    """mp4：-c copy 码流原样 + comment 元数据（免重编码）。"""
    src = _tiny_mp4(tmp_path / "src.mp4")
    out = tmp_path / "out" / "final.mp4"
    meta = {"app": "aivideo", "engine": "comfyui", "prompt": "雨夜街头"}
    remux_video(src, out, metadata=meta)

    assert out.is_file()
    assert read_video_metadata(out) == meta, "元数据未随 remux 写入"
    assert probe_resolution(out) == (64, 64)
    # copy 分支产物与源同码流：体积应接近（远小于重编码差异量级）
    assert out.stat().st_size >= src.stat().st_size * 0.5


def test_remux_video_transcodes_other_container(tmp_path):
    """非 mp4 容器（webm/gif）：video→video 转码，不经过 PNG 帧序列。"""
    src_mp4 = _tiny_mp4(tmp_path / "src.mp4")
    webm = tmp_path / "src.webm"
    shutil.copyfile(src_mp4, webm)  # 内容 mp4、后缀 webm：强制走转码分支
    out = tmp_path / "out" / "final.mp4"
    remux_video(webm, out, metadata={"k": "v"})

    assert read_video_metadata(out) == {"k": "v"}
    assert probe_resolution(out) == (64, 64)


def test_extract_first_frame(tmp_path):
    src = _tiny_mp4(tmp_path / "src.mp4")
    out = tmp_path / "frames" / "frame_0000.png"
    extract_first_frame(src, out)
    assert out.is_file() and out.stat().st_size > 0


# =====================================================================
# 队列层：原生视频直通 / remux 失败拷贝兜底 / 帧序列路径不变
# =====================================================================
class _NativeEngine:
    """假 ComfyUI 引擎：产出原生视频（返回空帧列表）。"""

    last_prompt_id = "pid_native"
    last_native_video = ""

    def generate(self, **kw):
        return []


class _FramesEngine:
    """假本地引擎：产出帧序列（无原生视频）。"""

    last_prompt_id = ""
    last_native_video = ""

    def __init__(self, frames):
        self._frames = frames

    def generate(self, **kw):
        return self._frames


@pytest.fixture()
def queue_env(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(qm, "OUTPUTS_DIR", out)
    monkeypatch.setattr(qm, "hw_probe", type("H", (), {"probe": staticmethod(
        lambda: type("P", (), {"to_dict": staticmethod(
            lambda: {"gpu_name": "t", "quant": "none", "offload": "none"})})())})())
    qm.job_queue._engine_instances.clear()
    return out


def _use_engine(monkeypatch, engine):
    monkeypatch.setattr(qm, "get_engine", lambda eid: (lambda: engine))


def _mk_shot(fresh_db):
    proj = sb.create_project("2c", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot = sb.create_shot(scene["id"], title="镜头1", prompt=GOOD_PROMPT, duration_s=4)
    return proj, scene, shot


def _mk_job(fresh_db, shot_id, engine="comfyui"):
    return fresh_db.create_job({
        "engine": engine, "prompt": GOOD_PROMPT, "width": 512, "height": 512,
        "num_frames": 16, "fps": 8, "steps": 25, "guidance": 7.5, "seed": 7,
        "shot_id": shot_id,
    })


def _forbidden(*a, **k):
    raise AssertionError("原生视频直通不应走帧序列合成")


def test_queue_native_video_passthrough(fresh_db, queue_env, monkeypatch):
    """队列：原生视频 remux 直通——免抽帧重编码、元数据入库、海报帧、清临时文件。"""
    proj, scene, shot = _mk_shot(fresh_db)
    native = _tiny_mp4(queue_env / "comfy_pid_native.mp4")
    engine = _NativeEngine()
    engine.last_native_video = str(native)
    _use_engine(monkeypatch, engine)
    monkeypatch.setattr(qm, "frames_to_video", _forbidden)

    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "done", done.get("error")
    final = queue_env / f"{job['id']}.mp4"
    assert final.is_file()
    assert read_video_metadata(final).get("app") == "aivideo", "remux 未写入生成参数"
    assert not native.exists(), "临时原生视频未清理"
    poster = queue_env / job["id"] / "frames" / "frame_0000.png"
    assert poster.is_file(), "海报帧未从成片抽取"
    after = sb.get_shot(shot["id"])
    assert after["status"] == "review" and after["review_stage"] == "video"


def test_queue_native_remux_failure_falls_back_to_copy(fresh_db, queue_env, monkeypatch):
    """remux 失败：原样拷贝兜底（丢元数据不丢成片），job 仍完成。"""
    proj, scene, shot = _mk_shot(fresh_db)
    native = queue_env / "comfy_pid_bad.mp4"
    native.write_bytes(b"NOT-A-REAL-VIDEO")
    engine = _NativeEngine()
    engine.last_native_video = str(native)
    _use_engine(monkeypatch, engine)
    monkeypatch.setattr(qm, "frames_to_video", _forbidden)

    def _boom_remux(*a, **k):
        raise RuntimeError("remux 炸了")

    monkeypatch.setattr(qm, "remux_video", _boom_remux)

    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "done", done.get("error")
    final = queue_env / f"{job['id']}.mp4"
    assert final.read_bytes() == b"NOT-A-REAL-VIDEO", "remux 失败未原样拷贝兜底"


def test_queue_frames_path_unchanged(fresh_db, queue_env, monkeypatch):
    """帧序列路径（本地引擎/图片序列输出）：行为不变。"""
    proj, scene, shot = _mk_shot(fresh_db)
    frames_dir = queue_env / "job_f" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame = frames_dir / "frame_0000.png"
    frame.write_bytes(PNG_BYTES)
    engine = _FramesEngine([frame])
    _use_engine(monkeypatch, engine)

    called = {}

    def _fake_f2v(frames, out, fps, metadata=None):
        called["args"] = (frames, out, fps)
        Path(out).write_bytes(b"\x00\x00\x00\x18ftypmp42" * 16)

    monkeypatch.setattr(qm, "frames_to_video", _fake_f2v)

    job = _mk_job(fresh_db, shot["id"], engine="anime")
    asyncio.run(qm.job_queue._run_job(job["id"]))

    assert "args" in called, "帧序列路径未被调用"
    assert fresh_db.get_job(job["id"])["status"] == "done"
    assert (queue_env / f"{job['id']}.mp4").is_file()


# =====================================================================
# 接口层：入队时上传参考图并写进快照 / 上传失败 400
# =====================================================================
def _router_ref_setup(client, tmp_path, monkeypatch, upload_impl):
    """建项目/场景/分镜 + 参考图文件 + 假模板/假上传，返回 shot_id。"""
    import app.routers.storyboard as sb_router
    import app.comfyui as comfyui_mod

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    monkeypatch.setattr(comfyui_mod, "OUTPUTS_DIR", out)
    ref = out / "uploads" / "ref.png"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(PNG_BYTES)

    monkeypatch.setattr(comfyui_mod, "load_workflow_template", lambda: {
        "prompt": {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
                   "10": {"class_type": "LoadImage", "inputs": {"image": ""}}},
        "_aivideo_map": {"prompt": {"node": "2", "input": "text"},
                         "reference_image": {"node": "10", "input": "image"}},
    })
    monkeypatch.setattr(ComfyUIClient, "upload_image", upload_impl)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)

    pid = client.post("/api/projects", json={"name": "2c", "description": ""}).json()["id"]
    client.post(f"/api/projects/{pid}/script/import",
                json={"text": "场景一：雨夜\n甲走在街上（远景, 固定, 4秒）", "mode": "rebuild"})
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={
        "prompt": GOOD_PROMPT, "reference_image": "/outputs/uploads/ref.png"})
    return sid


def test_router_generate_uploads_reference_into_snapshot(client, fresh_db, tmp_path, monkeypatch):
    """generate(comfyui, i2v)：入队即上传参考图，快照里是 input 目录引用名。"""
    uploaded = []

    def _fake_upload(self, path, name=""):
        uploaded.append(str(path))
        return {"name": "ref.png", "subfolder": "clipspace", "type": "input"}

    sid = _router_ref_setup(client, tmp_path, monkeypatch, _fake_upload)
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui", "mode": "i2v"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert uploaded, "入队时未上传参考图"
    assert job["workflow_snapshot"], "job 未记录快照路径"
    graph = json.loads(Path(job["workflow_snapshot"]).read_text(encoding="utf-8"))["graph"]
    assert graph["10"]["inputs"]["image"] == "clipspace/ref.png", "快照未注入 input 引用名"
    assert graph["2"]["inputs"]["text"] == GOOD_PROMPT


def test_router_generate_reference_upload_failure_400(client, fresh_db, tmp_path, monkeypatch):
    """参考图上传失败：接口层 400 明确告知（不进队列、不落半成品 job）。"""

    def _fake_upload(self, path, name=""):
        raise ComfyUIError("模拟上传失败")

    sid = _router_ref_setup(client, tmp_path, monkeypatch, _fake_upload)
    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui", "mode": "i2v"})
    assert r.status_code == 400
    assert "参考图上传 ComfyUI 失败" in r.json()["detail"]
