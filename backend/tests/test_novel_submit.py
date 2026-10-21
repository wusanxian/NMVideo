"""PR-N05：预览转正与安全提交回归测试（生成任务全打桩，不触引擎）。

覆盖：建批次前置校验（未审核/未交接/预算超限/final 未采用/i2v 缺参考图/
并发批次 409）、预览批次全链路（逐镜串行放行 → 任务完成 → 版本落库 →
批次 done）、部分提交、依赖图（前序失败 → 续接镜头明确失败、独立镜头
照常放行 → partial）、重试失败条目、取消（进行中不杀、未入队作废）、
活跃批次阻断分镜重建与重复提交、跨线程提交器在无事件循环时的显式失败、
交接回填镜头映射。
"""
import pytest

from test_novel_shot_plan import (
    _import,
    _patch_llm,
    _setup_approved_script,
    _wait_analysis,
)

SUBMIT_PLAN = {
    "scenes": [
        {"title": "竹林清晨", "shots": [
            {"title": "入林", "shot_size": "远景", "camera_movement": "推镜",
             "action": "林川沿小径走入竹林，薄雾弥漫", "dialogue": "",
             "characters": ["林川"], "position": "从画面左侧入画",
             "emotion": "压抑", "duration_sec": 4, "mode": "t2v"},
            {"title": "对峙", "shot_size": "中景", "camera_movement": "固定",
             "action": "苏檸按剑而立，目光冷冷逼视", "dialogue": "你终于来了。",
             "characters": ["林川", "苏檸"], "position": "画面中心对峙",
             "emotion": "紧张", "duration_sec": 5, "mode": "continuation"},
        ]},
        {"title": "剑拔弩张", "shots": [
            {"title": "喝止", "shot_size": "全景", "camera_movement": "摇镜",
             "action": "灰袍长老现身，拂袖喝止两人", "dialogue": "住手！",
             "characters": ["长老"], "position": "画面右侧入画",
             "emotion": "威严", "duration_sec": 3, "mode": "t2v"},
        ]},
    ]
}

I2V_PLAN = {
    "scenes": [{"title": "竹林", "shots": [
        # 首镜头强制 t2v（N04 规则），i2v 放第二镜
        {"title": "入林", "shot_size": "远景", "camera_movement": "推镜",
         "action": "林川走入竹林", "dialogue": "", "characters": ["林川"],
         "position": "", "emotion": "", "duration_sec": 4, "mode": "t2v"},
        {"title": "对视", "shot_size": "近景", "camera_movement": "固定",
         "action": "两人对视", "dialogue": "", "characters": ["林川"],
         "position": "", "emotion": "", "duration_sec": 4, "mode": "i2v"},
    ]}],
}

T2V_PLAN = {
    "scenes": [{"title": "竹林", "shots": [
        {"title": "入林", "shot_size": "远景", "camera_movement": "推镜",
         "action": "林川走入竹林", "dialogue": "", "characters": ["林川"],
         "position": "", "emotion": "", "duration_sec": 4, "mode": "t2v"},
    ]}],
}

# 流程测试统一用 comfyui（支持全部四模式；任务创建已打桩，不触真实服务）
ENGINE = "comfyui"

TERMINAL = ("done", "partial", "failed", "cancelled")


def _setup_ready(client, monkeypatch, tmp_path, *, plan=SUBMIT_PLAN):
    """导入 → 分析 → 圣经 → 分集 → 剧本审核 → 分镜 → 审核分镜 → 交接项目。

    返回 (novel_id, episode_id, plan)。
    """
    _patch_llm(monkeypatch, tmp_path, plan=plan)
    nid, eid = _setup_approved_script(client, monkeypatch, tmp_path)
    # _setup_approved_script 内部会用默认 PLAN_RESP 重打补丁——
    # 分镜生成前重新指认本测试的分镜响应
    _patch_llm(monkeypatch, tmp_path, plan=plan)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 200, r.text
    plan_data = r.json()
    assert client.post(
        f"/api/novels/{nid}/episodes/{eid}/shots/approve",
        json={}).status_code == 200
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/handoff")
    assert r.status_code == 200, r.text
    # 交接后重取计划（条目已回填 shot_id；生成响应是交接前的旧副本）
    plan_data = client.get(
        f"/api/novels/{nid}/episodes/{eid}/shots").json()
    return nid, eid, plan_data


def _fake_creator(fresh_db, outcomes=None, calls=None):
    """任务创建打桩：直接建 job 行并按 outcomes（shot_id → done/failed/
    running）落状态；done 时模拟 worker 联动（落候选版本 + 采用指针 +
    镜头进待审核）。返回可交给 monkeypatch 的函数。"""
    from app import storyboard as sb

    outcomes = outcomes or {}

    def create(shot_id, gen):
        if calls is not None:
            calls.append({"shot_id": shot_id, "gen": dict(gen)})
        job = fresh_db.create_job({
            "engine": gen.get("engine", "anime"), "prompt": "测试提示词",
            "width": gen.get("width", 416), "height": gen.get("height", 256),
            "num_frames": gen.get("num_frames", 17),
            "fps": gen.get("fps", 16), "steps": gen.get("steps", 12),
            "guidance": gen.get("guidance", 5.0), "seed": 1,
        })
        outcome = outcomes.get(shot_id, "done")
        if outcome == "done":
            _fake_finish_job(fresh_db, job["id"], shot_id)
        elif outcome == "failed":
            fresh_db.update_job(job["id"], status="failed", error="引擎爆炸")
        elif outcome == "running":
            fresh_db.update_job(job["id"], status="running", progress=0.3,
                                stage="生成中")
        return fresh_db.get_job(job["id"])

    return create


def _fake_finish_job(fresh_db, job_id, shot_id):
    """模拟 queue_manager._run_job 的镜头联动（打桩版）。"""
    from app import storyboard as sb
    fresh_db.update_job(job_id, status="done", progress=1.0,
                        video_url=f"/outputs/{job_id}.mp4")
    j = fresh_db.get_job(job_id)
    ver = sb.create_version(shot_id, j, f"/outputs/{job_id}.mp4", "")
    sb.update_shot(shot_id, current_version_id=ver["id"], job_id="")
    sb.set_shot_status(shot_id, "review", review_stage="video")


def _patch_submitter(monkeypatch, fresh_db, outcomes=None, calls=None):
    """打桩提交器：替换 _create_job_for_shot + 禁用后台线程（同步驱动）。"""
    from app import novel_submit as ns
    monkeypatch.setattr(ns, "_create_job_for_shot",
                        _fake_creator(fresh_db, outcomes, calls))
    monkeypatch.setattr(ns, "_ensure_watcher", lambda: None)


def _drive(client, nid, eid, ns, max_rounds=8):
    """轮询推进批次直到终态，返回最新批次。"""
    sub = None
    for _ in range(max_rounds):
        subs = client.get(
            f"/api/novels/{nid}/episodes/{eid}/shots/submissions").json()
        sub = subs[0]
        if sub["status"] in TERMINAL:
            return sub
        ns.advance_submission(sub["id"])
    return sub


# =====================================================================
# 建批次前置校验
# =====================================================================
def test_submit_requires_approved_and_handoff(client, fresh_db, monkeypatch,
                                              tmp_path):
    _patch_submitter(monkeypatch, fresh_db)
    # 计划未审核 → 400
    _patch_llm(monkeypatch, tmp_path)
    n = _import(client)
    nid = n["id"]
    client.post(f"/api/novels/{nid}/analyze", json={})
    _wait_analysis(client, nid)
    client.post(f"/api/novels/{nid}/bible/generate")
    eps = client.post(f"/api/novels/{nid}/episodes/generate", json={}).json()
    eid = eps[0]["id"]
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/generate")
    client.post(f"/api/novels/{nid}/episodes/{eid}/script/approve", json={})
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit", json={})
    assert r.status_code == 400 and "审核" in r.json()["detail"]
    # 已审核但未交接 → 400
    client.post(f"/api/novels/{nid}/episodes/{eid}/shots/approve", json={})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit", json={})
    assert r.status_code == 400 and "交接" in r.json()["detail"]


def test_submit_budget_limit(client, fresh_db, monkeypatch, tmp_path):
    _patch_submitter(monkeypatch, fresh_db)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    # lite 基准 120s × (4+5+3)/4 = 360s
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE, "budget_limit_sec": 60})
    assert r.status_code == 400 and "预算" in r.json()["detail"]
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    assert r.status_code == 201, r.text
    assert r.json()["budget_estimate_sec"] == 360
    assert r.json()["budget_limit_sec"] == 3600


def test_submit_final_requires_adopted_version(client, fresh_db, monkeypatch,
                                               tmp_path):
    _patch_submitter(monkeypatch, fresh_db)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE, "kind": "final",
                          "quality": "standard"})
    assert r.status_code == 400 and "采用" in r.json()["detail"]


def test_submit_i2v_requires_reference(client, fresh_db, monkeypatch,
                                       tmp_path):
    _patch_submitter(monkeypatch, fresh_db)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path,
                                  plan=I2V_PLAN)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    assert r.status_code == 400 and "参考图" in r.json()["detail"]


def test_submit_unknown_shot_item_404(client, fresh_db, monkeypatch, tmp_path):
    _patch_submitter(monkeypatch, fresh_db)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"shot_item_ids": ["nsi_not_exist"]})
    assert r.status_code == 404


def test_submit_anime_rejects_continuation(client, fresh_db, monkeypatch,
                                           tmp_path):
    """anime 引擎不支持续接 → 建批次前 400 明示（不留半截批次）。"""
    _patch_submitter(monkeypatch, fresh_db)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": "anime"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "continuation" in detail and "ComfyUI" in detail


def test_submit_anime_clamps_frames(client, fresh_db, monkeypatch, tmp_path):
    """anime 引擎 + 纯 t2v 计划：帧数按护栏钳到 32，预览可正常提交。"""
    calls = []
    _patch_submitter(monkeypatch, fresh_db, calls=calls)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path,
                                  plan=T2V_PLAN)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": "anime"})
    assert r.status_code == 201, r.text
    sub = _drive(client, nid, eid,
                 __import__("app.novel_submit", fromlist=["x"]))
    assert sub["status"] == "done"
    assert calls[0]["gen"]["num_frames"] == 32  # 4s × 16fps = 64 → 钳 32


# =====================================================================
# 预览批次全链路
# =====================================================================
def test_preview_submission_happy_path(client, fresh_db, monkeypatch, tmp_path):
    calls = []
    _patch_submitter(monkeypatch, fresh_db, calls=calls)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    items = plan["items"]

    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["kind"] == "preview" and sub["engine"] == ENGINE
    assert sub["shot_count"] == 3 and len(sub["items"]) == 3
    assert sub["items"][0]["status"] == "done"  # 首镜已放行且（打桩）即时完成
    assert sub["items"][0]["adopted_version_id"] == ""  # 预览前无采用版本

    sub = _drive(client, nid, eid,
                 __import__("app.novel_submit", fromlist=["x"]))
    assert sub["status"] == "done", sub
    assert [it["status"] for it in sub["items"]] == ["done"] * 3
    # 任务按 seq 串行放行（依赖顺序）
    assert [c["shot_id"] for c in calls] == \
        [it["shot_id"] for it in sub["items"]]
    # 预览参数：lite 档 + preview=True；帧数按时长×16fps（anime 钳到 32）
    g0 = calls[0]["gen"]
    assert g0["preview"] is True and g0["preset"] == "lite"
    assert g0["num_frames"] == 64  # 4s × 16fps（comfyui 无帧数钳制）
    assert g0["mode"] == items[0]["mode"]
    # 打桩 worker 联动：镜头有采用版本、进待审核
    from app import storyboard as sb
    for it in sub["items"]:
        shot = sb.get_shot(it["shot_id"])
        assert shot["current_version_id"]
        assert shot["status"] == "review"


def test_partial_submission_shot_ids(client, fresh_db, monkeypatch, tmp_path):
    _patch_submitter(monkeypatch, fresh_db)
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    items = plan["items"]
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE,
                          "shot_item_ids": [items[0]["id"], items[2]["id"]]})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["shot_count"] == 2
    sub = _drive(client, nid, eid,
                 __import__("app.novel_submit", fromlist=["x"]))
    assert sub["status"] == "done"
    assert [it["seq"] for it in sub["items"]] == [1, 3]


# =====================================================================
# 依赖图与失败隔离
# =====================================================================
def test_continuation_after_failure_isolated(client, fresh_db, monkeypatch,
                                             tmp_path):
    """镜头1 失败 → 续接镜头2 明确失败；独立镜头3 照常放行 → partial。"""
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    from app import novel_submit as ns
    shot1 = plan["items"][0]["shot_id"]
    _patch_submitter(monkeypatch, fresh_db, outcomes={shot1: "failed"})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    assert r.status_code == 201, r.text
    sub = _drive(client, nid, eid, ns)
    assert sub["status"] == "partial", sub
    by_seq = {it["seq"]: it for it in sub["items"]}
    assert by_seq[1]["status"] == "failed" and "爆炸" in by_seq[1]["error"]
    assert by_seq[2]["status"] == "failed" and "续接" in by_seq[2]["error"]
    assert by_seq[3]["status"] == "done"  # 失败隔离：独立镜头不受牵连


# =====================================================================
# 重试 / 取消
# =====================================================================
def test_retry_failed_items(client, fresh_db, monkeypatch, tmp_path):
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    from app import novel_submit as ns
    shot1 = plan["items"][0]["shot_id"]
    state = {"shot1_failed": True}

    def create(shot_id, gen):
        base = _fake_creator(fresh_db,
                             {shot1: "failed"} if state["shot1_failed"] else {})
        return base(shot_id, gen)

    monkeypatch.setattr(ns, "_create_job_for_shot", create)
    monkeypatch.setattr(ns, "_ensure_watcher", lambda: None)
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    sub = _drive(client, nid, eid, ns)
    assert sub["status"] == "partial", sub
    # 重试：镜头1 改为成功，只重试失败条目
    state["shot1_failed"] = False
    r = client.post(
        f"/api/novels/{nid}/episodes/{eid}/shots/submissions/"
        f"{sub['id']}/retry")
    assert r.status_code == 200, r.text
    sub = _drive(client, nid, eid, ns)
    assert sub["status"] == "done", sub
    assert all(it["status"] == "done" for it in sub["items"])


def test_cancel_leaves_running_job(client, fresh_db, monkeypatch, tmp_path):
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    from app import novel_submit as ns
    shot1 = plan["items"][0]["shot_id"]
    _patch_submitter(monkeypatch, fresh_db, outcomes={shot1: "running"})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    sub = r.json()
    assert sub["status"] == "running"
    assert sub["items"][0]["status"] == "running"
    # 取消：进行中的条目不杀，未入队条目作废
    r = client.post(
        f"/api/novels/{nid}/episodes/{eid}/shots/submissions/"
        f"{sub['id']}/cancel")
    assert r.status_code == 200, r.text
    sub = r.json()
    assert sub["status"] == "cancelling"
    assert sub["items"][0]["status"] == "running"
    assert sub["items"][1]["status"] == "cancelled"
    assert sub["items"][2]["status"] == "cancelled"
    # 进行中的生成自然完成 → 批次转「已取消」（完成镜头保留成果）
    _fake_finish_job(fresh_db, sub["items"][0]["job_id"], shot1)
    ns.advance_submission(sub["id"])
    subs = client.get(
        f"/api/novels/{nid}/episodes/{eid}/shots/submissions").json()
    assert subs[0]["status"] == "cancelled"
    assert subs[0]["items"][0]["status"] == "done"


# =====================================================================
# 并发闸与重建阻断
# =====================================================================
def test_generate_plan_blocked_by_active_submission(client, fresh_db,
                                                    monkeypatch, tmp_path):
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    from app import novel_submit as ns
    shot1 = plan["items"][0]["shot_id"]
    _patch_submitter(monkeypatch, fresh_db, outcomes={shot1: "running"})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    assert r.status_code == 201
    # 活跃批次存在 → 重新生成分镜被阻断（409）
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 409 and "批次" in r.json()["detail"]
    # 进行中生成自然完成 → 批次取消结算 → 可重建
    sub = client.get(
        f"/api/novels/{nid}/episodes/{eid}/shots/submissions").json()[0]
    client.post(
        f"/api/novels/{nid}/episodes/{eid}/shots/submissions/"
        f"{sub['id']}/cancel")
    _fake_finish_job(fresh_db, sub["items"][0]["job_id"], shot1)
    ns.advance_submission(sub["id"])
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/generate")
    assert r.status_code == 200, r.text


def test_submit_duplicate_active_409(client, fresh_db, monkeypatch, tmp_path):
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    shot1 = plan["items"][0]["shot_id"]
    _patch_submitter(monkeypatch, fresh_db, outcomes={shot1: "running"})
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    assert r.status_code == 201
    r = client.post(f"/api/novels/{nid}/episodes/{eid}/shots/submit",
                    json={"engine": ENGINE})
    assert r.status_code == 409 and "批次" in r.json()["detail"]


# =====================================================================
# 打桩之外的真实护栏
# =====================================================================
def test_create_job_without_loop_raises(monkeypatch, fresh_db):
    """无事件循环（服务未启动）时跨线程提交显式失败，不静默。"""
    from app import novel_submit as ns
    monkeypatch.setattr(ns, "_LOOP", None)
    with pytest.raises(ns.SubmitError) as ei:
        ns._create_job_for_shot("shot_x", {"engine": "anime"})
    assert "尚未就绪" in str(ei.value)


def test_handoff_records_shot_id(client, fresh_db, monkeypatch, tmp_path):
    """交接后条目回填项目镜头映射（提交批次的定位基础）。"""
    nid, eid, plan = _setup_ready(client, monkeypatch, tmp_path)
    rows = fresh_db.fetch_all(
        "SELECT seq, shot_id FROM novel_shot_items"
        " WHERE plan_id = (SELECT id FROM novel_shot_plans"
        " WHERE episode_id = ?) ORDER BY seq", (eid,))
    assert len(rows) == 3
    assert all(r["shot_id"] for r in rows)
    from app import storyboard as sb
    for r in rows:
        assert sb.get_shot(r["shot_id"])
