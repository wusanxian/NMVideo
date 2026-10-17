"""3A 数据保护门禁测试：审核三态 / 质检前置 / 引用保护 / 导入保护 / 队列联动。

覆盖审查项：
- S1：审核质检前置——阻断时 current_version_id 与状态均保持原值；
- S3：导入预览绑定（content_hash + project_revision），过期提交拒绝，
      破坏性 rebuild 未确认拒绝（带损失明细），确认后先备份再重建；
- I1/O1：删除/换指针的引用保护（时间线片段、进行中任务）→ 409 + 明细；
- 审核三态：design / generation / video 的流转与留痕；
- queue_manager 完成路径：review_stage=video；被时间线引用的镜头
  生成完成只追加候选、不静默更换采用指针。
"""
import asyncio
from pathlib import Path

import pytest

from app import queue_manager as qm
from app import storyboard as sb

SCRIPT_A = "场景一：教室\n同学走进教室（中景, 固定, 3秒）\n场景二：操场\n大家在跑步"
SCRIPT_B = "场景一：雨夜\n甲独自走在街上（远景, 跟, 4秒）\n乙追上来"

GOOD_PROMPT = "雨夜街头，霓虹灯下的长镜头，湿地反光"


# =====================================================================
# helpers
# =====================================================================
def _mk_project_scene_shot(fresh_db, prompt=GOOD_PROMPT, title="镜头1"):
    proj = sb.create_project("p3a", "")
    scene = sb.create_scene(proj["id"], "场景一")
    shot = sb.create_shot(scene["id"], title=title, prompt=prompt, duration_s=4)
    return proj, scene, shot


def _mk_job(fresh_db, shot_id="", engine="fake"):
    return fresh_db.create_job({
        "engine": engine, "prompt": GOOD_PROMPT, "width": 512, "height": 512,
        "num_frames": 16, "fps": 8, "steps": 25, "guidance": 7.5, "seed": 7,
        "shot_id": shot_id,
    })


def _mk_version(fresh_db, shot_id, video_name="v.mp4", with_file=True,
                outputs_dir=None):
    """落一个候选版本；with_file=True 时在受管 outputs 目录写一个非空文件。

    版本来源任务标记为 done——否则 pending 任务会被 shot_references
    当作「进行中的任务」计入引用，污染引用守卫类测试。
    """
    job = _mk_job(fresh_db, shot_id)
    fresh_db.update_job(job["id"], status="done")
    url = f"/outputs/{video_name}"
    if with_file:
        assert outputs_dir is not None, "with_file 需要 outputs_dir"
        (outputs_dir / video_name).parent.mkdir(parents=True, exist_ok=True)
        (outputs_dir / video_name).write_bytes(b"\x00\x00\x00\x18ftypmp42" * 16)
    return sb.create_version(shot_id, job, url, "")


@pytest.fixture()
def outputs(tmp_path, monkeypatch):
    """把受管 outputs 目录指到临时目录（storyboard / shotcheck 均运行时读取）。"""
    import app.config as config

    d = tmp_path / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "OUTPUTS_DIR", d)
    return d


def _approve_into_timeline(project_id, shot_id):
    """把镜头（已当前采用版本）送进时间线，返回时间线片段列表。"""
    shot = sb.get_shot(shot_id)
    sb.update_shot(shot_id, current_version_id=shot["versions"][0]["id"])
    sb.set_shot_status(shot_id, "review")
    return sb.sync_timeline(project_id)


# =====================================================================
# 审核三态：design / generation / video
# =====================================================================
def test_design_review_blocked_keeps_stage(fresh_db):
    """设计审核：提示词过短 → ReviewBlockedError，review_stage 不变。"""
    _, _, shot = _mk_project_scene_shot(fresh_db, prompt="短")
    sb.set_shot_status(shot["id"], "draft", review_stage="design")
    with pytest.raises(sb.ReviewBlockedError) as ei:
        sb.review_shot(shot["id"], "approve", stage="design")
    codes = {i["code"] for i in ei.value.issues}
    assert "prompt_thin" in codes
    assert ei.value.score["level"] == "error"
    after = sb.get_shot(shot["id"])
    assert after["review_stage"] == "design", "阻断后设计审核标记被清掉"
    assert after["status"] == "draft", "设计审核不应改动生成状态机"
    # 阻断也留痕
    project_id = sb.get_scene(after["scene_id"])["project_id"]
    hist = sb.list_review_history(project_id)
    assert any(h["action"] == "design_blocked" for h in hist)


def test_design_review_approve_sets_design_ok(fresh_db):
    _, _, shot = _mk_project_scene_shot(fresh_db)
    result = sb.review_shot(shot["id"], "approve", stage="design")
    assert result["review_stage"] == "design_ok"
    assert result["status"] == "draft", "设计审核不推进生成状态机"


def test_design_review_rework_sets_design(fresh_db):
    _, _, shot = _mk_project_scene_shot(fresh_db)
    result = sb.review_shot(shot["id"], "rework", stage="design", comment="再改改")
    assert result["review_stage"] == "design"


def test_generation_stage_set_on_queue(fresh_db):
    """generate_shot 入队时 review_stage=generation（router 同一写法）。"""
    _, _, shot = _mk_project_scene_shot(fresh_db)
    job = _mk_job(fresh_db, shot["id"])
    sb.set_shot_status(shot["id"], "queued", job_id=job["id"],
                       review_stage="generation")
    after = sb.get_shot(shot["id"])
    assert after["status"] == "queued" and after["review_stage"] == "generation"


# =====================================================================
# S1：视频审核质检前置 + 原子写入
# =====================================================================
def _video_review_setup(fresh_db, outputs, video_name="v.mp4", with_file=True):
    proj, _, shot = _mk_project_scene_shot(fresh_db)
    ver = _mk_version(fresh_db, shot["id"], video_name, with_file, outputs)
    sb.update_shot(shot["id"], current_version_id=ver["id"])
    sb.set_shot_status(shot["id"], "review", review_stage="video")
    return proj, shot, ver


def test_review_approve_blocked_by_qa_keeps_state(fresh_db, outputs):
    """视频文件缺失 → 422 级阻断；指针与状态均保持原值（修 S1）。"""
    _, shot, ver = _video_review_setup(fresh_db, outputs, with_file=False)
    with pytest.raises(sb.ReviewBlockedError) as ei:
        sb.review_shot(shot["id"], "approve")
    codes = {i["code"] for i in ei.value.issues}
    assert "video_missing" in codes
    after = sb.get_shot(shot["id"])
    assert after["current_version_id"] == ver["id"], "阻断后采用指针被改写"
    assert after["status"] == "review", "阻断后状态被改写"
    assert after["review_stage"] == "video"


def test_review_approve_writes_atomically(fresh_db, outputs):
    proj, shot, ver = _video_review_setup(fresh_db, outputs)
    result = sb.review_shot(shot["id"], "approve", comment="OK")
    assert result["status"] == "approved"
    assert result["review_stage"] == ""
    assert result["current_version_id"] == ver["id"]
    hist = sb.list_review_history(proj["id"])
    assert any(h["action"] == "approve" and h["stage"] == "video" for h in hist)


def test_review_approve_rejects_missing_file_version(fresh_db, outputs):
    _, shot, ver = _video_review_setup(fresh_db, outputs, with_file=False)
    with pytest.raises(ValueError, match="缺失或不可读"):
        sb.review_shot(shot["id"], "approve", version_id=ver["id"])
    assert sb.get_shot(shot["id"])["status"] == "review"


def test_review_approve_rejects_foreign_version(fresh_db, outputs):
    _, _, shot_a = _mk_project_scene_shot(fresh_db, title="A")
    _, _, shot_b = _mk_project_scene_shot(fresh_db, title="B")
    ver_b = _mk_version(fresh_db, shot_b["id"], "vb.mp4", True, outputs)
    sb.update_shot(shot_b["id"], current_version_id=ver_b["id"])
    sb.set_shot_status(shot_b["id"], "review")
    with pytest.raises(ValueError, match="不属于该镜头"):
        sb.review_shot(shot_a["id"], "approve", version_id=ver_b["id"])


def test_review_approve_stale_submission(fresh_db, outputs):
    """expected_version_id 与当前采用版本不一致 → 409 级过期提交。"""
    _, shot, ver = _video_review_setup(fresh_db, outputs)
    ver2 = _mk_version(fresh_db, shot["id"], "v2.mp4", True, outputs)
    with pytest.raises(sb.StaleSubmissionError):
        sb.review_shot(shot["id"], "approve", expected_version_id=ver2["id"])


def test_adopt_version_requires_file_and_ownership(fresh_db, outputs):
    _, _, shot_a = _mk_project_scene_shot(fresh_db, title="A")
    _, _, shot_b = _mk_project_scene_shot(fresh_db, title="B")
    ver_a = _mk_version(fresh_db, shot_a["id"], "va.mp4", True, outputs)
    ver_b = _mk_version(fresh_db, shot_b["id"], "vb.mp4", False, outputs)
    # 跨镜头采用 → 拒绝
    with pytest.raises(ValueError, match="不属于该镜头"):
        sb.adopt_version(shot_a["id"], ver_b["id"])
    # 文件缺失 → 拒绝
    with pytest.raises(ValueError, match="缺失或不可读"):
        sb.adopt_version(shot_b["id"], ver_b["id"])
    # 合法采用 → 指针切换
    sb.adopt_version(shot_a["id"], ver_a["id"])
    assert sb.get_shot(shot_a["id"])["current_version_id"] == ver_a["id"]


def test_approved_shot_switching_version_returns_to_video_review(fresh_db, outputs):
    """已通过镜头换采用版本 → 新版本不继承 approved，回到视频审核。"""
    proj, shot, ver = _video_review_setup(fresh_db, outputs)
    sb.review_shot(shot["id"], "approve")
    ver2 = _mk_version(fresh_db, shot["id"], "v2.mp4", True, outputs)
    sb.adopt_version(shot["id"], ver2["id"])
    after = sb.get_shot(shot["id"])
    assert after["status"] == "review" and after["review_stage"] == "video"
    assert after["current_version_id"] == ver2["id"]


# =====================================================================
# I1/O1：引用保护（时间线片段 / 进行中任务）
# =====================================================================
def _timeline_guard_setup(fresh_db, outputs):
    proj, _, shot = _mk_project_scene_shot(fresh_db)
    ver = _mk_version(fresh_db, shot["id"], "v.mp4", True, outputs)
    sb.update_shot(shot["id"], current_version_id=ver["id"])
    sb.set_shot_status(shot["id"], "review")
    sb.sync_timeline(proj["id"])
    return proj, shot, ver


def test_timeline_referenced_shot_cannot_switch_pointer(fresh_db, outputs):
    proj, shot, ver = _timeline_guard_setup(fresh_db, outputs)
    ver2 = _mk_version(fresh_db, shot["id"], "v2.mp4", True, outputs)
    with pytest.raises(sb.ReferenceConflictError) as ei:
        sb.review_shot(shot["id"], "approve", version_id=ver2["id"])
    assert ei.value.details["timeline_items"], "引用明细应带时间线片段"
    with pytest.raises(sb.ReferenceConflictError):
        sb.adopt_version(shot["id"], ver2["id"])
    assert sb.get_shot(shot["id"])["current_version_id"] == ver["id"], "指针被静默更换"


def test_delete_current_version_of_referenced_shot_refused(fresh_db, outputs):
    proj, shot, ver = _timeline_guard_setup(fresh_db, outputs)
    with pytest.raises(sb.ReferenceConflictError):
        sb.delete_version(shot["id"], ver["id"])
    # 非当前版本可删
    ver2 = _mk_version(fresh_db, shot["id"], "v2.mp4", True, outputs)
    sb.delete_version(shot["id"], ver2["id"])


def test_delete_referenced_shot_scene_project_refused(fresh_db, outputs):
    proj, shot, ver = _timeline_guard_setup(fresh_db, outputs)
    scene_id = shot["scene_id"]
    with pytest.raises(sb.ReferenceConflictError):
        sb.delete_shot(shot["id"])
    with pytest.raises(sb.ReferenceConflictError):
        sb.delete_scene(scene_id)
    with pytest.raises(sb.ReferenceConflictError):
        sb.delete_project(proj["id"])
    # 移除时间线引用后即可删除
    item = sb.list_timeline(proj["id"])[0]
    sb.remove_timeline_item(item["id"])
    sb.delete_shot(shot["id"])
    assert sb.get_shot(shot["id"]) is None


def test_active_job_blocks_delete(fresh_db):
    """进行中的任务同样是引用（删除镜头/项目被拒）。"""
    proj, scene, shot = _mk_project_scene_shot(fresh_db)
    _mk_job(fresh_db, shot["id"])  # pending
    refs = sb.shot_references(shot["id"])
    assert refs["active_jobs"], "进行中任务未被计入引用"
    with pytest.raises(sb.ReferenceConflictError):
        sb.delete_shot(shot["id"])
    with pytest.raises(sb.ReferenceConflictError):
        sb.delete_project(proj["id"])


def test_job_references_reports_versions(fresh_db, outputs):
    """任务产物被版本引用时 job_references 可查出（router 删任务守卫同源）。"""
    _, _, shot = _mk_project_scene_shot(fresh_db)
    ver = _mk_version(fresh_db, shot["id"], "v.mp4", True, outputs)
    refs = sb.job_references(ver["job_id"])
    assert any(v["id"] == ver["id"] for v in refs["versions"])


# =====================================================================
# S3：导入保护（预览绑定 / 过期提交 / 破坏性重建确认 + 备份）
# =====================================================================
def _rich_project(fresh_db, outputs):
    """带场景 + 已生成版本 + 时间线的项目。"""
    proj = sb.create_project("rich", "")
    sb.import_script(proj["id"], SCRIPT_A, mode="rebuild")
    scene = sb.list_scenes(proj["id"])[0]
    shot = scene["shots"][0]
    ver = _mk_version(fresh_db, shot["id"], "v.mp4", True, outputs)
    sb.update_shot(shot["id"], current_version_id=ver["id"])
    sb.set_shot_status(shot["id"], "review")
    sb.sync_timeline(proj["id"])
    return proj


def test_script_preview_binds_hash_and_revision(fresh_db, outputs):
    proj = _rich_project(fresh_db, outputs)
    pv = sb.script_preview(proj["id"], SCRIPT_B)
    assert pv["content_hash"] and len(pv["content_hash"]) == 64
    assert pv["project_revision"] == 1  # 已导入过一次
    assert pv["existing"]["shot_count"] >= 1


def test_import_rejects_hash_mismatch(fresh_db, outputs):
    proj = _rich_project(fresh_db, outputs)
    with pytest.raises(ValueError, match="content_hash"):
        sb.import_script(proj["id"], SCRIPT_B, content_hash="0" * 64)


def test_import_rejects_stale_prevision(fresh_db, outputs):
    proj = _rich_project(fresh_db, outputs)
    pv = sb.script_preview(proj["id"], SCRIPT_B)
    # 预览之后项目又发生一次导入（revision +1）→ 旧预览过期
    sb.import_script(proj["id"], SCRIPT_B, mode="append")
    with pytest.raises(sb.StalePreviewError):
        sb.import_script(proj["id"], SCRIPT_B, expected_revision=pv["project_revision"],
                         content_hash=pv["content_hash"])


def test_destructive_rebuild_requires_confirm_then_backs_up(fresh_db, outputs):
    proj = _rich_project(fresh_db, outputs)
    pv = sb.script_preview(proj["id"], SCRIPT_B)

    with pytest.raises(sb.DestructiveImportError) as ei:
        sb.import_script(proj["id"], SCRIPT_B,
                         expected_revision=pv["project_revision"],
                         content_hash=pv["content_hash"])
    assert ei.value.details["version_count"] >= 1
    assert ei.value.details["timeline_count"] >= 1
    # 未确认时数据原样
    assert [s["title"] for s in sb.list_scenes(proj["id"])] == ["教室", "操场"]

    result = sb.import_script(proj["id"], SCRIPT_B, confirm=True)
    assert result["revision"] == pv["project_revision"] + 1
    assert result["backup"]
    from pathlib import Path
    assert Path(result["backup"]).is_file(), "确认后未生成备份"
    assert [s["title"] for s in sb.list_scenes(proj["id"])] == ["雨夜"]
    assert sb.list_timeline(proj["id"]) == [], "重建后时间线应清空"


def test_rebuild_without_achievements_needs_no_confirm(fresh_db):
    """空项目重建不要求确认（无成果可损失）。"""
    proj = sb.create_project("empty", "")
    result = sb.import_script(proj["id"], SCRIPT_B, mode="rebuild")
    assert result["backup"] is None
    assert [s["title"] for s in sb.list_scenes(proj["id"])] == ["雨夜"]


# =====================================================================
# queue_manager 完成路径：review_stage / 时间线引用不换指针
# =====================================================================
class _FakeEngine:
    def generate(self, **kwargs):
        return []


class _FakeHW:
    def probe(self):
        class _P:
            def to_dict(self):
                return {"gpu_name": "test-gpu", "quant": "none", "offload": "none"}
        return _P()


@pytest.fixture()
def fake_engine(tmp_path, monkeypatch):
    """假引擎 + 假合成（落一个非空 mp4）+ 临时 outputs，跑通 _run_job 完成路径。

    fake_engine 与 outputs 两个 fixture 共用同一 tmp_path/outputs：
    qm.OUTPUTS_DIR 决定 video_url 的落盘位置，app.config.OUTPUTS_DIR
    决定 _video_path_ok 的校验位置，两边必须一致，审核 QA 才过得去。
    """
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)

    # job_queue 是进程级单例，_engine_instances 缓存会跨测试残留——
    # 不清缓存的话 monkeypatch 的 get_engine 根本不生效（拿到旧引擎实例）
    qm.job_queue._engine_instances.clear()

    def _fake_frames_to_video(frames_dir, out_path, fps, metadata=None):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x00\x00\x00\x18ftypmp42" * 16)

    monkeypatch.setattr(qm, "OUTPUTS_DIR", out)
    monkeypatch.setattr(qm, "get_engine", lambda engine_id: _FakeEngine)
    monkeypatch.setattr(qm, "frames_to_video", _fake_frames_to_video)
    monkeypatch.setattr(qm, "hw_probe", _FakeHW())
    return out


def test_queue_completion_sets_video_stage(fresh_db, fake_engine, outputs):
    proj, _, shot = _mk_project_scene_shot(fresh_db)
    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))

    done = fresh_db.get_job(job["id"])
    assert done["status"] == "done" and done["video_url"]
    after = sb.get_shot(shot["id"])
    assert after["status"] == "review"
    assert after["review_stage"] == "video"
    assert len(after["versions"]) == 1
    assert after["current_version_id"] == after["versions"][0]["id"]


def test_queue_completion_keeps_pointer_for_timeline_shot(fresh_db, fake_engine, outputs):
    """已入时间线的镜头：再次生成完成只追加候选，不换采用指针（3A）。"""
    proj, _, shot = _mk_project_scene_shot(fresh_db)
    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))
    v1 = sb.get_shot(shot["id"])["current_version_id"]

    # 审核通过并送入时间线
    sb.review_shot(shot["id"], "approve")
    sb.sync_timeline(proj["id"])
    assert sb.list_timeline(proj["id"])

    job2 = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job2["id"]))
    after = sb.get_shot(shot["id"])
    assert len(after["versions"]) == 2, "候选版本未追加"
    assert after["current_version_id"] == v1, "时间线镜头的采用指针被静默更换"
    assert after["status"] == "review" and after["review_stage"] == "video"
    # 时间线片段仍指向原版本
    assert sb.list_timeline(proj["id"])[0]["current_version_id"] == v1


def test_queue_failure_clears_review_stage(fresh_db, fake_engine, monkeypatch):
    """生成失败：镜头回 rework，视频审核标记被清除。"""
    proj, _, shot = _mk_project_scene_shot(fresh_db)
    sb.set_shot_status(shot["id"], "queued", review_stage="generation")

    class _BoomEngine:
        def generate(self, **kwargs):
            raise RuntimeError("注入：引擎炸了")

    monkeypatch.setattr(qm, "get_engine", lambda engine_id: _BoomEngine)
    job = _mk_job(fresh_db, shot["id"])
    asyncio.run(qm.job_queue._run_job(job["id"]))

    assert fresh_db.get_job(job["id"])["status"] == "failed"
    after = sb.get_shot(shot["id"])
    assert after["status"] == "rework" and after["review_stage"] == ""
