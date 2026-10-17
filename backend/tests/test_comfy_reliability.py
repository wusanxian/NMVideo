"""2B ComfyUI 任务可靠性测试：client_id 一致 / WS 静默期对账 / prompt_id 持久化 /
恢复执行 / 工作流快照 / 取消路径 / 重启探测。

覆盖审查项 2B：
- 提交与 WS 订阅共用同一 client_id（否则进度推送收不到）
- WS 无消息时定期查历史（连接前已完成/推送丢失也能发现）
- WS 不可用自动降级轮询；websockets 未安装不炸
- prompt_id 持久化到 job；重启后按 prompt_id 探测决定 续/完成/失败
- 入队即固化工作流快照；执行优先读快照（排队后不读可变模板）
- 取消走 GenerationCancelled（job 标 cancelled、镜头回 draft），
  **不调用全局 /interrupt**（HF-06：共享 ComfyUI 会误伤其他客户端）

Hotfix 追加（改造.md §2）：
- HF-00：last_native_video/last_prompt_id 每轮 generate 入口清零，
  原生视频不跨任务继承（串片回归）
- HF-04：probe 区分 temporarily_unavailable（断线待核查 needs_attention，
  不误标失败）与 lost（确认没有）；恢复分页拉取不截断；手动重试端点
- HF-05：快照存在但损坏 → 明确失败，不静默改用当前模板
"""
import asyncio
import json
from pathlib import Path

import pytest

from app import queue_manager as qm
from app import storyboard as sb
from app.comfyui import ComfyUICancelled, ComfyUIClient, ComfyUIError
from app.engines.base import GenerationCancelled
from app.engines.comfy import ComfyEngine

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x18ftypmp42" * 8
GOOD_PROMPT = "雨夜街头，霓虹灯下的长镜头，湿地反光"

_COMPLETED_ENTRY = {
    "status": {"completed": True, "status_str": "success"},
    "outputs": {"7": {"images": [
        {"filename": "aivideo_00001.png", "subfolder": "", "type": "output"},
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


def _mk_job(fresh_db, shot_id="", engine="comfyui", **extra):
    params = {
        "engine": engine, "prompt": GOOD_PROMPT, "width": 512, "height": 512,
        "num_frames": 16, "fps": 8, "steps": 25, "guidance": 7.5, "seed": 7,
        "shot_id": shot_id,
    }
    params.update(extra)
    return fresh_db.create_job(params)


# =====================================================================
# 客户端层：client_id 一致 / 静默期对账 / 降级 / probe
# =====================================================================
class _FakeWS:
    """永远超时的 WS 连接：模拟"连上了但收不到任何消息"。"""

    def __init__(self, log):
        self._log = log

    def recv(self, timeout=None):
        raise TimeoutError()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_http(monkeypatch, handler):
    """按 URL 分发假 requests.get/post。"""
    class _Resp:
        def __init__(self, data, code=200):
            self._data, self.status_code = data, code

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        @property
        def text(self):
            return json.dumps(self._data)

    class _Requests:
        def get(self, url, **kw):
            return handler(url)

        def post(self, url, **kw):
            return handler(url)

    import app.comfyui as comfyui_mod

    monkeypatch.setattr(comfyui_mod, "requests", _Requests())


def test_ws_url_carries_same_client_id_and_silent_history_check(monkeypatch):
    """WS URL 带提交时同一 client_id；无消息时靠历史轮询发现完成。"""
    import websockets.sync.client as wsc

    urls = []

    def fake_connect(url, **kw):
        urls.append(url)
        return _FakeWS(urls)

    monkeypatch.setattr(wsc, "connect", fake_connect)
    _patch_http(monkeypatch, lambda url: type(
        "R", (), {"status_code": 200, "json": lambda self: {"pid1": _COMPLETED_ENTRY}}
    )())

    c = ComfyUIClient("http://127.0.0.1:8188")
    entry = c.wait_for_completion("pid1", client_id="aivideo-abc123")
    assert entry and entry["status"]["completed"], "静默期未查历史兜底"
    assert urls and "clientId=aivideo-abc123" in urls[0], (
        f"WS 连接的 clientId 与提交不一致: {urls}"
    )


def test_ws_failure_falls_back_to_poll(monkeypatch):
    """WS 连接失败 → 自动降级历史轮询并正常拿到结果。"""
    import websockets.sync.client as wsc

    def fake_connect(url, **kw):
        raise ConnectionRefusedError("ComfyUI 没开")

    monkeypatch.setattr(wsc, "connect", fake_connect)
    _patch_http(monkeypatch, lambda url: type(
        "R", (), {"status_code": 200, "json": lambda self: {"pid1": _COMPLETED_ENTRY}}
    )())

    c = ComfyUIClient("http://127.0.0.1:8188")
    entry = c.wait_for_completion("pid1", client_id="aivideo-abc", timeout_s=10)
    assert entry and entry["status"]["completed"], "WS 失败后未降级轮询"


def test_resume_without_client_id_goes_straight_to_poll(monkeypatch):
    """resume 场景（client_id 丢失）不建 WS，直接轮询。"""
    import websockets.sync.client as wsc

    called = []

    def fake_connect(url, **kw):
        called.append(url)
        raise AssertionError("resume 不应建 WS 连接")

    monkeypatch.setattr(wsc, "connect", fake_connect)
    _patch_http(monkeypatch, lambda url: type(
        "R", (), {"status_code": 200, "json": lambda self: {"pid1": _COMPLETED_ENTRY}}
    )())

    c = ComfyUIClient("http://127.0.0.1:8188")
    entry = c.wait_for_completion("pid1", client_id="", timeout_s=10)
    assert entry and not called


def test_probe_states(monkeypatch):
    """probe：done / queued / error / lost 四种状态判定。"""
    def handler(url):
        if "/history/" in url:
            return type("R", (), {
                "status_code": 200,
                "json": lambda self: {"pid": _COMPLETED_ENTRY},
            })()
        return type("R", (), {
            "status_code": 200,
            "json": lambda self: {"queue_running": [], "queue_pending": [[3, "pid", {}, {}, []]]},
        })()

    _patch_http(monkeypatch, handler)
    c = ComfyUIClient("http://127.0.0.1:8188")
    assert c.probe("pid") == "done", "已完成任务应判 done（history 优先于队列）"

    # 未完成 + 在 pending 队列 → queued
    _patch_http(monkeypatch, lambda url: type("R", (), {
        "status_code": 200,
        "json": lambda self: ({"pid": {"status": {"status_str": "error"}}}
                              if "/history/" in url
                              else {"queue_running": [[1, "pid", {}, {}, []]],
                                    "queue_pending": []}),
    })())
    assert c.probe("pid") == "error"

    # 历史与队列都没有 → lost
    _patch_http(monkeypatch, lambda url: type("R", (), {
        "status_code": 200, "json": lambda self: {},
    })())
    assert c.probe("pid") == "lost"


def test_poll_cancel_does_not_global_interrupt(monkeypatch):
    """HF-06：取消**不**触发全局 /interrupt——共享 ComfyUI 时会误伤其他
    客户端的任务；仅本地标记取消，提示远端可能仍在执行。"""
    interrupted = []

    class _Requests:
        def post(self, url, **kw):
            if url.endswith("/interrupt"):
                interrupted.append(url)
            return type("R", (), {"status_code": 200, "json": lambda self: {}})()

        def get(self, url, **kw):
            return type("R", (), {"status_code": 200, "json": lambda self: {}})()

    import app.comfyui as comfyui_mod

    monkeypatch.setattr(comfyui_mod, "requests", _Requests())
    c = ComfyUIClient("http://127.0.0.1:8188")
    with pytest.raises(ComfyUICancelled):
        c.wait_for_completion("pid", is_cancelled=lambda: True, timeout_s=5)
    assert not interrupted, "取消时不应调用全局 interrupt（HF-06）"


# =====================================================================
# 引擎层：client_id 一致 / 取消转换 / 快照优先
# =====================================================================
class _FakeClient:
    """记录调用参数的可假客户端。"""

    def __init__(self):
        self.calls = {}

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
        return _COMPLETED_ENTRY

    def download(self, filename, subfolder="", ftype="output"):
        return PNG_BYTES


def _mk_engine(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.engines.comfy.OUTPUTS_DIR", out)
    engine = ComfyEngine()
    engine.client = _FakeClient()
    return engine, out


def _gen_kwargs(frames_dir):
    return dict(
        prompt=GOOD_PROMPT, negative_prompt="", width=512, height=512,
        num_frames=16, fps=8, steps=25, guidance=7.5, seed=7,
        frames_dir=frames_dir, on_progress=lambda p, s: None,
        is_cancelled=lambda: False,
    )


def test_engine_submit_and_ws_share_client_id(tmp_path, monkeypatch):
    engine, _ = _mk_engine(tmp_path, monkeypatch)
    frames = engine.generate(**_gen_kwargs(tmp_path / "jobA" / "frames"))
    assert frames, "假产物未落帧"
    assert engine.last_prompt_id == "pid_fake"
    submit_cid = engine.client.calls["submit"]["client_id"]
    wait_cid = engine.client.calls["wait"]["client_id"]
    assert submit_cid == wait_cid == "aivideo-fixedcid", (
        f"提交与 WS 的 client_id 不一致: {submit_cid} vs {wait_cid}"
    )


def test_engine_cancel_converted_to_generation_cancelled(tmp_path, monkeypatch):
    engine, _ = _mk_engine(tmp_path, monkeypatch)

    class _CancelClient(engine.client.__class__):
        def wait_for_completion(self, *a, **k):
            raise ComfyUICancelled("已取消")

    engine.client = _CancelClient()
    with pytest.raises(GenerationCancelled):
        engine.generate(**_gen_kwargs(tmp_path / "jobB" / "frames"))


def test_engine_prefers_snapshot_over_template(tmp_path, monkeypatch):
    """执行优先用入队快照——排队期间模板被改也不影响已排队任务。"""
    import app.comfyui as comfyui_mod

    engine, out = _mk_engine(tmp_path, monkeypatch)
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"job_id": "jobC", "graph": {"2": {"inputs": {"text": "快照提示词"}}}}),
                    encoding="utf-8")
    # 模板被改成另一个样子
    monkeypatch.setattr(comfyui_mod, "load_workflow_template",
                        lambda: {"prompt": {"2": {"inputs": {"text": "改过的模板"}}},
                                 "_aivideo_map": {}})
    graph = engine._resolve_workflow(str(snap), {"prompt": "参数"}, tmp_path / "jobC" / "frames")
    assert graph["2"]["inputs"]["text"] == "快照提示词", "未优先使用快照"


def test_engine_fallback_writes_snapshot(tmp_path, monkeypatch):
    """无快照时现场读模板构建，并补写一份独立快照（每任务存档）。"""
    import app.comfyui as comfyui_mod

    engine, out = _mk_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(comfyui_mod, "load_workflow_template",
                        lambda: {"prompt": {"2": {"inputs": {"text": "x"}}},
                                 "_aivideo_map": {"prompt": {"node": "2", "input": "text"}}})
    graph = engine._resolve_workflow("", {"prompt": "注入提示词"},
                                     tmp_path / "jobD" / "frames")
    assert graph["2"]["inputs"]["text"] == "注入提示词"
    snap = out / "workflows" / "jobD.json"
    assert snap.is_file(), "兜底路径未补写快照"
    assert json.loads(snap.read_text(encoding="utf-8"))["graph"]["2"]["inputs"]["text"] == "注入提示词"


def test_build_shot_workflow_injects_params(monkeypatch, tmp_path):
    """build_shot_workflow：按 _aivideo_map 注入参数（入队快照与引擎兜底共用）。"""
    from app.comfyui import build_shot_workflow

    monkeypatch.setattr("app.comfyui.DEFAULT_WORKFLOW", tmp_path / "wf.json")
    (tmp_path / "wf.json").write_text(json.dumps({
        "prompt": {"2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
                   "5": {"class_type": "KSampler", "inputs": {"seed": 0}}},
        "_aivideo_map": {"prompt": {"node": "2", "input": "text"},
                         "seed": {"node": "5", "input": "seed"}},
    }), encoding="utf-8")
    graph = build_shot_workflow({"prompt": "测试提示词", "seed": 42, "steps": 30})
    assert graph["2"]["inputs"]["text"] == "测试提示词"
    assert graph["5"]["inputs"]["seed"] == 42


# =====================================================================
# 队列层：prompt_id 持久化 / resume 跳过提交 / 取消标记 / 重启探测
# =====================================================================
@pytest.fixture()
def fake_engine_env(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(qm, "OUTPUTS_DIR", out)
    monkeypatch.setattr(qm, "frames_to_video",
                        lambda *a, **k: Path(a[1]).write_bytes(b"\x00\x00\x00\x18ftypmp42" * 16))
    monkeypatch.setattr(qm, "hw_probe", type("H", (), {"probe": staticmethod(
        lambda: type("P", (), {"to_dict": staticmethod(
            lambda: {"gpu_name": "t", "quant": "none", "offload": "none"})})())})())
    qm.job_queue._engine_instances.clear()
    return out


def _use_engine(monkeypatch, engine):
    monkeypatch.setattr(qm, "get_engine", lambda eid: (lambda: engine))


def test_queue_persists_prompt_id(fresh_db, fake_engine_env, monkeypatch):
    """正常流程：提交后 prompt_id 落库（重启恢复/对账的前提）。"""
    proj = sb.create_project("cb", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot = sb.create_shot(scene["id"], title="镜头1", prompt=GOOD_PROMPT, duration_s=4)
    engine, _ = _mk_engine(fake_engine_env, monkeypatch)
    _use_engine(monkeypatch, engine)
    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "done"
    assert done["prompt_id"] == "pid_fake", "prompt_id 未持久化"
    assert engine.client.calls["submit"]["client_id"] == engine.client.calls["wait"]["client_id"]


def test_queue_resume_skips_submit(fresh_db, fake_engine_env, monkeypatch):
    """恢复流程：job 带 prompt_id → 跳过提交直接等（client_id 为空=轮询）。"""
    proj = sb.create_project("cb2", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot = sb.create_shot(scene["id"], title="镜头1", prompt=GOOD_PROMPT, duration_s=4)
    engine, _ = _mk_engine(fake_engine_env, monkeypatch)
    _use_engine(monkeypatch, engine)
    job = _mk_job(fresh_db, shot["id"])
    fresh_db.update_job(job["id"], prompt_id="pid_resume", status="running")
    asyncio.run(qm.job_queue._run_job(job["id"]))

    assert "submit" not in engine.client.calls, "恢复流程不应重新提交"
    assert engine.client.calls["wait"]["prompt_id"] == "pid_resume"
    assert engine.client.calls["wait"]["client_id"] == "", "resume 应走轮询（client_id 已丢失）"
    assert fresh_db.get_job(job["id"])["status"] == "done"


def test_queue_cancel_marks_cancelled(fresh_db, fake_engine_env, monkeypatch):
    """取消：job 标 cancelled（不是 failed），镜头回 draft 且清审核标记。"""
    proj = sb.create_project("cb3", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot = sb.create_shot(scene["id"], title="镜头1", prompt=GOOD_PROMPT, duration_s=4)
    sb.set_shot_status(shot["id"], "generating", review_stage="generation")

    class _CancelEngine:
        def generate(self, **kw):
            raise GenerationCancelled()

    _use_engine(monkeypatch, _CancelEngine())
    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "cancelled", "取消的 job 不应标 failed"
    after = sb.get_shot(shot["id"])
    assert after["status"] == "draft" and after["review_stage"] == ""


def _stale_running_job(fresh_db, shot_id="", prompt_id=""):
    proj = sb.create_project("rec", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot = sb.create_shot(scene["id"], title="镜头1", prompt=GOOD_PROMPT, duration_s=4)
    job = _mk_job(fresh_db, shot["id"] or shot["id"], prompt_id=prompt_id)
    fresh_db.update_job(job["id"], status="running", prompt_id=prompt_id)
    if shot_id:
        fresh_db.update_job(job["id"], shot_id=shot_id)
    return proj, shot, job


def test_recover_comfy_running_job_reenqueues(fresh_db, monkeypatch):
    """重启恢复：ComfyUI 侧仍在 → 重新入队（resume），镜头保持 generating。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_x")
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job", staticmethod(lambda pid: "running"))
    qm.job_queue._recover_stale_jobs()
    assert not qm.job_queue._queue.empty(), "可恢复的 ComfyUI 任务未重新入队"
    assert qm.job_queue._queue.get_nowait() == job["id"]
    after = sb.get_shot(shot["id"])
    assert after["status"] == "generating" and after["review_stage"] == "generation"


def test_recover_comfy_lost_job_fails(fresh_db, monkeypatch):
    """重启恢复：ComfyUI 历史与队列都没有 → 标中断失败，镜头打回重做。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_gone")
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job", staticmethod(lambda pid: "lost"))
    qm.job_queue._recover_stale_jobs()
    done = fresh_db.get_job(job["id"])
    assert done["status"] == "failed" and "中断" in (done["error"] or "")
    after = sb.get_shot(shot["id"])
    assert after["status"] == "rework" and after["review_stage"] == ""


def test_recover_comfy_error_job_fails(fresh_db, monkeypatch):
    """重启恢复：ComfyUI 侧已失败 → 明确告知重启前已失败。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_err")
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job", staticmethod(lambda pid: "error"))
    qm.job_queue._recover_stale_jobs()
    done = fresh_db.get_job(job["id"])
    assert done["status"] == "failed" and "重启前已执行失败" in (done["error"] or "")


def test_recover_local_engine_job_fails(fresh_db, monkeypatch):
    """重启恢复：本地引擎（无 prompt_id）一律标记中断（无断点续算）。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="")
    qm.job_queue._recover_stale_jobs()
    done = fresh_db.get_job(job["id"])
    assert done["status"] == "failed" and "中断" in (done["error"] or "")


# =====================================================================
# 接口层：入队即固化工作流快照
# =====================================================================
def test_generate_shot_writes_workflow_snapshot(client, fresh_db, tmp_path, monkeypatch):
    """generate(engine=comfyui)：入队即落工作流快照，job 记录快照路径。"""
    import app.routers.storyboard as sb_router

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)

    pid = client.post("/api/projects", json={"name": "snap", "description": ""}).json()["id"]
    client.post(f"/api/projects/{pid}/script/import",
                json={"text": "场景一：雨夜\n甲走在街上（远景, 固定, 4秒）", "mode": "rebuild"})
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})

    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["workflow_snapshot"], "job 未记录工作流快照路径"
    from pathlib import Path

    snap = Path(job["workflow_snapshot"])
    assert snap.is_file(), "快照文件不存在"
    graph = json.loads(snap.read_text(encoding="utf-8"))["graph"]
    assert graph, "快照里没有节点图"
    # 分镜进入生成审核门
    after = client.get(f"/api/shots/{sid}").json()
    assert after["status"] == "queued" and after["review_stage"] == "generation"


# =====================================================================
# P0-01：提交即持久化（on_submitted 先于 _wait；等待期崩溃可对账）
# =====================================================================
def test_engine_on_submitted_fires_before_wait(tmp_path, monkeypatch):
    """P0-01：/prompt 返回即触发 on_submitted，先于 _wait——ComfyUI 已受理
    但本服务在等待期间崩溃时，库里已有 prompt_id/client_id 可对账。"""
    engine, _ = _mk_engine(tmp_path, monkeypatch)
    events = []

    class _WaitClient(engine.client.__class__):
        def wait_for_completion(self, prompt_id, **kw):
            events.append(("wait", prompt_id, kw.get("client_id", "")))
            return _COMPLETED_ENTRY

    engine.client = _WaitClient()
    frames = engine.generate(
        **_gen_kwargs(tmp_path / "jobE" / "frames"),
        on_submitted=lambda pid, cid: events.append(("submitted", pid, cid)),
    )
    assert frames, "假产物未落帧"
    assert events[0][0] == "submitted", f"on_submitted 未先于 wait 触发: {events}"
    assert events[0][1] == "pid_fake" and events[0][2] == "aivideo-fixedcid"
    assert events[1][0] == "wait"


def test_engine_on_submitted_fires_even_if_wait_raises(tmp_path, monkeypatch):
    """P0-01：等待期间崩溃（wait 抛异常）也已完成提交落库——
    重启恢复靠这个 prompt_id 决定续跑而不是重复提交。"""
    engine, _ = _mk_engine(tmp_path, monkeypatch)

    class _BoomClient(engine.client.__class__):
        def wait_for_completion(self, *a, **k):
            raise ComfyUIError("模拟 ComfyUI 生成中本服务被杀")

    engine.client = _BoomClient()
    submitted = []
    with pytest.raises(ComfyUIError):
        engine.generate(
            **_gen_kwargs(tmp_path / "jobF" / "frames"),
            on_submitted=lambda pid, cid: submitted.append((pid, cid)),
        )
    assert submitted == [("pid_fake", "aivideo-fixedcid")], (
        f"提交回调未在等待前完成: {submitted}"
    )


def test_engine_on_submitted_exception_does_not_break_generation(tmp_path, monkeypatch):
    """P0-01：持久化回调本身失败不阻断生成（重启时按 lost 标失败）。"""
    engine, _ = _mk_engine(tmp_path, monkeypatch)

    def bad_submitted(pid, cid):
        raise RuntimeError("模拟落库失败")

    frames = engine.generate(**_gen_kwargs(tmp_path / "jobG" / "frames"),
                             on_submitted=bad_submitted)
    assert frames, "on_submitted 抛异常不应阻断生成"


def test_queue_prompt_id_persisted_before_wait_completes(
    fresh_db, fake_engine_env, monkeypatch
):
    """P0-01 队列级：提交成功、等待阻塞期间进程被杀 → job 行已持久化
    prompt_id + client_id（重启恢复与对账的前提；旧实现在 generate 返回后
    才写库，等待期崩溃即丢账）。"""
    proj = sb.create_project("p0-1-crash", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot = sb.create_shot(scene["id"], title="镜头1", prompt=GOOD_PROMPT, duration_s=4)

    class _CrashDuringWait:
        """submit 已返回（on_submitted 落库完成），随后进程被杀。"""
        last_prompt_id = ""

        def generate(self, **kw):
            on_submitted = kw.get("on_submitted")
            assert on_submitted, "队列未传 on_submitted"
            on_submitted("pid_crash", "aivideo-crashcid")
            raise RuntimeError("进程在等待期间被杀")

    _use_engine(monkeypatch, _CrashDuringWait())
    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))

    row = fresh_db.get_job(job["id"])
    assert row["status"] == "failed"
    assert row["prompt_id"] == "pid_crash", (
        "提交即持久化缺失：等待期崩溃后重启无法对账，可能重复提交"
    )
    assert row["client_id"] == "aivideo-crashcid", "client_id 未随 prompt_id 落库"


def test_restart_reconcile_resume_completes(fresh_db, fake_engine_env, monkeypatch):
    """P0-01 全流程：提交即落库 → 等待期崩溃 → 重启探测 ComfyUI 仍在 →
    重新入队 resume（不重复提交、wait 带持久化 client_id）→ 完成归档。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_reconcile")
    fresh_db.update_job(job["id"], client_id="aivideo-reconcilecid")

    # 重启：ComfyUI 侧任务还在跑 → 重新入队
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job",
                        staticmethod(lambda pid: "running"))
    qm.job_queue._recover_stale_jobs()
    assert qm.job_queue._queue.get_nowait() == job["id"], "可恢复任务未重新入队"

    # resume 执行：不提交、wait 续用持久化 client_id
    engine, _ = _mk_engine(fake_engine_env, monkeypatch)
    _use_engine(monkeypatch, engine)
    asyncio.run(qm.job_queue._run_job(job["id"]))

    assert "submit" not in engine.client.calls, "恢复流程重复提交了任务"
    wait_call = engine.client.calls["wait"]
    assert wait_call["prompt_id"] == "pid_reconcile"
    assert wait_call["client_id"] == "aivideo-reconcilecid", (
        "恢复未续用提交时持久化的 client_id（WS 进度接不上，只能轮询）"
    )
    done = fresh_db.get_job(job["id"])
    assert done["status"] == "done" and done["video_url"], "恢复任务未完成归档"


# =====================================================================
# P0-02：上传失败不留孤立任务（备料先行，成功后才建 job）
# =====================================================================
def _mk_shot_for_generate(client, name):
    pid = client.post("/api/projects",
                      json={"name": name, "description": ""}).json()["id"]
    client.post(f"/api/projects/{pid}/script/import",
                json={"text": "场景一：雨夜\n甲走在街上（远景, 固定, 4秒）",
                      "mode": "rebuild"})
    sid = client.get(f"/api/projects/{pid}/scenes").json()[0]["shots"][0]["id"]
    client.patch(f"/api/shots/{sid}", json={"prompt": GOOD_PROMPT})
    return pid, sid


def _patch_template_maps_reference(monkeypatch, tmp_path):
    """让模板 _aivideo_map 映射 reference_image，从而走上传分支。"""
    import app.comfyui as comfyui_mod

    real = comfyui_mod.load_workflow_template()
    monkeypatch.setattr(
        comfyui_mod, "load_workflow_template",
        lambda: {**real, "_aivideo_map": {
            **(real.get("_aivideo_map") or {}),
            "reference_image": {"node": "1", "input": "image"},
        }},
    )


def test_upload_failure_leaves_no_job(client, fresh_db, tmp_path, monkeypatch):
    """P0-02：参考图上传失败 → 400，DB 不落任何可执行任务。

    旧顺序先 create_job 后上传：HTTPException 400 不回滚，留下无
    prompt_id 的 pending 僵尸任务，重启后还会被恢复器捡起来重跑。
    """
    import app.comfyui as comfyui_mod
    import app.routers.storyboard as sb_router

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    _patch_template_maps_reference(monkeypatch, tmp_path)

    def boom(client, url):
        raise ComfyUIError("模拟上传失败")

    monkeypatch.setattr(comfyui_mod, "upload_reference_image", boom)

    pid, sid = _mk_shot_for_generate(client, "p0-2-fail")
    client.patch(f"/api/shots/{sid}",
                 json={"reference_image": "/outputs/refs/x.png"})
    before = len(fresh_db.list_jobs(limit=1000))

    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "i2v"})
    assert r.status_code == 400, r.text
    assert "参考图上传" in r.text

    after = fresh_db.list_jobs(limit=1000)
    assert len(after) == before, "上传失败后仍创建了 job"
    assert not [j for j in after if j["status"] in ("pending", "running")], (
        "上传失败留下了可执行的孤立任务"
    )
    shot = client.get(f"/api/shots/{sid}").json()
    assert shot["status"] != "queued", "上传失败后分镜被拖入 queued"


def test_empty_template_rejected_before_job_creation(
    client, fresh_db, tmp_path, monkeypatch
):
    """P0-02 预检：工作流模板为空/缺 prompt 节点图 → 400，不建 job。"""
    import app.comfyui as comfyui_mod
    import app.routers.storyboard as sb_router

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    monkeypatch.setattr(comfyui_mod, "load_workflow_template",
                        lambda: {"_aivideo_map": {}})  # 没有 prompt 节点图

    pid, sid = _mk_shot_for_generate(client, "p0-2-empty")
    before = len(fresh_db.list_jobs(limit=1000))

    r = client.post(f"/api/shots/{sid}/generate", json={"engine": "comfyui"})
    assert r.status_code == 400, r.text
    assert "工作流模板为空" in r.text
    assert len(fresh_db.list_jobs(limit=1000)) == before, "预检失败后仍创建了 job"


def test_upload_success_creates_job_with_snapshot(client, fresh_db, tmp_path,
                                                  monkeypatch):
    """P0-02 成功路径：上传成功 → 201 + 恰好一个可执行任务 + 快照落盘。"""
    import app.comfyui as comfyui_mod
    import app.routers.storyboard as sb_router

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sb_router, "OUTPUTS_DIR", out)
    monkeypatch.setattr(ComfyUIClient, "is_available", lambda self, timeout=2.5: True)
    _patch_template_maps_reference(monkeypatch, tmp_path)
    monkeypatch.setattr(comfyui_mod, "upload_reference_image",
                        lambda client, url: "refs/uploaded.png")

    pid, sid = _mk_shot_for_generate(client, "p0-2-ok")
    client.patch(f"/api/shots/{sid}",
                 json={"reference_image": "/outputs/refs/x.png"})
    before = len(fresh_db.list_jobs(limit=1000))

    r = client.post(f"/api/shots/{sid}/generate",
                    json={"engine": "comfyui", "mode": "i2v"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["workflow_snapshot"], "成功路径未记录快照"
    snap = Path(job["workflow_snapshot"])
    assert snap.is_file(), "快照文件不存在"
    graph = json.loads(snap.read_text(encoding="utf-8"))["graph"]
    assert graph, "快照里没有节点图"

    jobs = fresh_db.list_jobs(limit=1000)
    assert len(jobs) == before + 1, "成功路径应恰好新增一个 job"
    runnable = [j for j in jobs if j["status"] in ("pending", "running")]
    assert len(runnable) == 1 and runnable[0]["id"] == job["id"]
    shot = client.get(f"/api/shots/{sid}").json()
    assert shot["status"] == "queued" and shot["job_id"] == job["id"]


# =====================================================================
# HF-00：last_native_video 跨任务污染（原生视频串片）
# =====================================================================
_VIDEO_ENTRY = {
    "status": {"completed": True, "status_str": "success"},
    "outputs": {"7": {"videos": [
        {"filename": "aivideo_00001.mp4", "subfolder": "", "type": "output"},
    ]}},
}


class _AlternatingClient:
    """第 1/3/5… 次提交产原生视频，第 2/4/6… 次产图片序列。

    模拟「任务 A 出 MP4、任务 B 只出图片帧」的交替场景——队列缓存同一
    引擎实例，B 不得继承 A 的 last_native_video（HF-00）。
    """

    def __init__(self):
        self.submits = 0

    def is_available(self, timeout=2.5):
        return True

    def new_client_id(self, prefix="aivideo"):
        return "aivideo-alt"

    def submit(self, graph, client_id="aivideo"):
        self.submits += 1
        return f"pid_alt{self.submits}"

    def wait_for_completion(self, prompt_id, on_progress=None, is_cancelled=None,
                            timeout_s=3600, client_id=""):
        if self.submits % 2 == 1:
            return _VIDEO_ENTRY
        return _COMPLETED_ENTRY

    def download(self, filename, subfolder="", ftype="output"):
        return b"MP4DATA-" + filename.encode()


def test_engine_native_video_not_inherited_across_jobs(tmp_path, monkeypatch):
    """HF-00：同一引擎实例交替 MP4/图片序列——图片轮不得带上轮的原生视频。"""
    engine, _ = _mk_engine(tmp_path, monkeypatch)
    engine.client = _AlternatingClient()

    f1 = engine.generate(**_gen_kwargs(tmp_path / "altA" / "frames"))
    assert f1 == [] and engine.last_native_video, "第一轮应走原生视频直通"
    first_native = engine.last_native_video

    f2 = engine.generate(**_gen_kwargs(tmp_path / "altB" / "frames"))
    assert f2, "第二轮应下载图片帧"
    assert engine.last_native_video == "", (
        f"第二轮继承了上一轮的原生视频: {engine.last_native_video}（串片！）"
    )

    f3 = engine.generate(**_gen_kwargs(tmp_path / "altC" / "frames"))
    assert f3 == [] and engine.last_native_video, "第三轮应恢复原生视频直通"
    assert engine.last_native_video != first_native


def test_engine_no_stale_native_video_after_failure(tmp_path, monkeypatch):
    """HF-00：上一轮产视频、本轮失败——失败后不得残留旧 native_video。"""
    engine, _ = _mk_engine(tmp_path, monkeypatch)
    engine.client = _AlternatingClient()
    engine.generate(**_gen_kwargs(tmp_path / "failA" / "frames"))
    assert engine.last_native_video

    class _BoomClient(_AlternatingClient):
        def wait_for_completion(self, *a, **k):
            raise ComfyUIError("模拟生成中失败")

    engine.client = _BoomClient()
    with pytest.raises(ComfyUIError):
        engine.generate(**_gen_kwargs(tmp_path / "failB" / "frames"))
    assert engine.last_native_video == "", "失败后仍带着上一轮的原生视频"


def test_queue_native_video_not_inherited(fresh_db, fake_engine_env, monkeypatch):
    """HF-00 队列级（真实 ComfyEngine + 假客户端）：A=原生视频、B=图片序列，
    B 的成片必须来自 B 的帧，不是 A 的视频（串片回归，worker 归档路径）。"""
    proj = sb.create_project("hf00q", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot_a = sb.create_shot(scene["id"], title="A", prompt=GOOD_PROMPT, duration_s=4)
    shot_b = sb.create_shot(scene["id"], title="B", prompt=GOOD_PROMPT, duration_s=4)

    engine, _ = _mk_engine(fake_engine_env, monkeypatch)
    engine.client = _AlternatingClient()
    _use_engine(monkeypatch, engine)

    job_a = _mk_job(fresh_db, shot_a["id"])
    asyncio.run(qm.job_queue._run_job(job_a["id"]))
    assert fresh_db.get_job(job_a["id"])["status"] == "done"
    final_a = fake_engine_env / f"{job_a['id']}.mp4"
    assert final_a.read_bytes() == b"MP4DATA-aivideo_00001.mp4", "A 未走原生视频归档"

    job_b = _mk_job(fresh_db, shot_b["id"])
    asyncio.run(qm.job_queue._run_job(job_b["id"]))
    assert fresh_db.get_job(job_b["id"])["status"] == "done"
    final_b = fake_engine_env / f"{job_b['id']}.mp4"
    assert final_b.read_bytes() != b"MP4DATA-aivideo_00001.mp4", (
        "B 串片了：归档了 A 的原生视频而不是 B 的帧序列"
    )


# =====================================================================
# HF-04：断线待核查（temporarily_unavailable）+ 分页恢复 + 手动重试
# =====================================================================
def test_probe_temporarily_unavailable_on_network_error(monkeypatch):
    """HF-04：ComfyUI 网络异常 → temporarily_unavailable（≠ lost，不误判）。"""
    import app.comfyui as comfyui_mod

    class _Requests:
        def get(self, url, **kw):
            raise ConnectionError("连不上 ComfyUI")

        def post(self, url, **kw):
            raise ConnectionError("连不上 ComfyUI")

    monkeypatch.setattr(comfyui_mod, "requests", _Requests())
    c = ComfyUIClient("http://127.0.0.1:8188")
    assert c.probe("pid") == "temporarily_unavailable"


def test_probe_lost_only_when_reachable_and_absent(monkeypatch):
    """HF-04：只有 history/队列都明确应答"没有"才判 lost。"""
    _patch_http(monkeypatch, lambda url: type("R", (), {
        "status_code": 200, "json": lambda self: {},
    })())
    c = ComfyUIClient("http://127.0.0.1:8188")
    assert c.probe("pid") == "lost"


def test_recover_comfy_unavailable_marks_needs_attention(fresh_db, monkeypatch):
    """HF-04：ComfyUI 暂不可达 → needs_attention 待核查：不误标失败、
    不重新入队，镜头保持 generating。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_net")
    # 崩溃前镜头已由 worker 置 generating（真实场景的持久化状态）
    sb.set_shot_status(shot["id"], "generating", review_stage="generation")
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job",
                        staticmethod(lambda pid: "temporarily_unavailable"))
    qm.job_queue._recover_stale_jobs()

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "needs_attention", "断线被误标为其他状态"
    assert "暂不可达" in (done["error"] or "") + (done["stage"] or "")
    assert qm.job_queue._queue.empty(), "待核查任务不应入队"
    after = sb.get_shot(shot["id"])
    assert after["status"] == "generating", "待核查不应把镜头打回重做"
    assert after["review_stage"] == "generation"


def test_recover_needs_attention_resumes_when_back(fresh_db, monkeypatch):
    """HF-04：待核查任务下次重启时 ComfyUI 恢复 → 重新入队 resume。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_back")
    fresh_db.update_job(job["id"], status="needs_attention")
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job",
                        staticmethod(lambda pid: "running"))
    qm.job_queue._recover_stale_jobs()

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "running"
    assert not qm.job_queue._queue.empty()
    assert qm.job_queue._queue.get_nowait() == job["id"]
    after = sb.get_shot(shot["id"])
    assert after["status"] == "generating"


def test_recover_needs_attention_stays_when_still_unavailable(fresh_db, monkeypatch):
    """HF-04：仍不可达 → 继续待核查，绝不误标失败。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_still")
    fresh_db.update_job(job["id"], status="needs_attention")
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job",
                        staticmethod(lambda pid: "temporarily_unavailable"))
    qm.job_queue._recover_stale_jobs()

    assert fresh_db.get_job(job["id"])["status"] == "needs_attention"
    assert qm.job_queue._queue.empty()


def test_recover_needs_attention_confirmed_lost_fails(fresh_db, monkeypatch):
    """HF-04：待核查后确认 ComfyUI 侧也没有（lost）→ 标中断失败。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_gone2")
    fresh_db.update_job(job["id"], status="needs_attention")
    monkeypatch.setattr(qm.JobQueue, "_probe_comfy_job",
                        staticmethod(lambda pid: "lost"))
    qm.job_queue._recover_stale_jobs()

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "failed" and "中断" in (done["error"] or "")
    assert sb.get_shot(shot["id"])["status"] == "rework"


def test_iter_jobs_paginates_beyond_one_page(fresh_db):
    """HF-04：_iter_jobs 分页拉取，超过一页（_RECOVER_PAGE）也能取全且保序。"""
    for _ in range(qm.JobQueue._RECOVER_PAGE + 3):
        fresh_db.create_job({
            "engine": "anime", "prompt": GOOD_PROMPT, "width": 512, "height": 512,
            "num_frames": 16, "fps": 8, "steps": 25, "guidance": 7.5, "seed": 1,
        })
    got = list(qm.job_queue._iter_jobs("pending", order="asc"))
    assert len(got) == qm.JobQueue._RECOVER_PAGE + 3, "分页拉取遗漏了任务"


def test_recover_reenqueues_beyond_one_page(fresh_db):
    """HF-04：恢复时分页重新入队，超过一页的 pending 不截断（原 limit=1000）。"""
    for _ in range(qm.JobQueue._RECOVER_PAGE + 3):
        fresh_db.create_job({
            "engine": "anime", "prompt": GOOD_PROMPT, "width": 512, "height": 512,
            "num_frames": 16, "fps": 8, "steps": 25, "guidance": 7.5, "seed": 1,
        })
    qm.job_queue._recover_stale_jobs()
    assert qm.job_queue._queue.qsize() == qm.JobQueue._RECOVER_PAGE + 3


def test_retry_needs_attention_job(client, fresh_db):
    """HF-04：needs_attention 任务可手动重试（重新入队走 resume）。"""
    proj, shot, job = _stale_running_job(fresh_db, prompt_id="pid_retry")
    fresh_db.update_job(job["id"], status="needs_attention")
    r = client.post(f"/api/jobs/{job['id']}/retry")
    assert r.status_code == 200, r.text
    done = fresh_db.get_job(job["id"])
    assert done["status"] == "running"
    assert not qm.job_queue._queue.empty()
    assert qm.job_queue._queue.get_nowait() == job["id"]


def test_retry_rejects_non_attention_job(client, fresh_db):
    """HF-04：非待核查状态不可重试（409，语义未定义）。"""
    proj, shot, job = _stale_running_job(fresh_db)
    r = client.post(f"/api/jobs/{job['id']}/retry")
    assert r.status_code == 409


# =====================================================================
# HF-05：快照损坏显式失败（不静默改用当前模板）
# =====================================================================
def test_engine_corrupt_snapshot_fails_explicitly(tmp_path, monkeypatch):
    """HF-05：快照文件损坏 → ComfyUIError 明确失败，不退回当前模板。"""
    engine, _ = _mk_engine(tmp_path, monkeypatch)
    snap = tmp_path / "corrupt.json"
    snap.write_text("{invalid json", encoding="utf-8")
    with pytest.raises(ComfyUIError) as ei:
        engine._resolve_workflow(str(snap), {"prompt": "x"},
                                 tmp_path / "jobH" / "frames")
    assert "快照" in str(ei.value), "错误信息未说明是快照问题"


def test_engine_snapshot_without_graph_fails(tmp_path, monkeypatch):
    """HF-05：快照是合法 JSON 但没有有效节点图 → 同样明确失败。"""
    engine, _ = _mk_engine(tmp_path, monkeypatch)
    snap = tmp_path / "empty_graph.json"
    snap.write_text(json.dumps({"job_id": "x", "graph": {}}), encoding="utf-8")
    with pytest.raises(ComfyUIError) as ei:
        engine._resolve_workflow(str(snap), {"prompt": "x"},
                                 tmp_path / "jobI" / "frames")
    assert "快照" in str(ei.value)
