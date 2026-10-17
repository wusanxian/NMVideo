"""2A 事务边界测试：import_script 单事务重建 / 回滚 / append / insert_and_get。

审查项 S3 相关：剧本文本更新必须与重建同一事务——重建失败时
旧剧本、旧场景、旧镜头、时间线一个都不能少。
"""
import pytest

from app import storyboard as sb

SCRIPT_A = "场景一：教室\n同学走进教室（中景, 固定, 3秒）\n场景二：操场\n大家在跑步"
SCRIPT_A2 = "场景一：雨夜\n甲独自走在街上（远景, 跟, 4秒）\n乙追上来"


def test_import_rebuild_rollback_keeps_everything(fresh_db, monkeypatch):
    proj = sb.create_project("rb", "")
    first = sb.import_script(proj["id"], SCRIPT_A, mode="rebuild")
    assert first["scene_count"] == 2
    scenes_before = [s["title"] for s in sb.list_scenes(proj["id"])]
    script_before = sb.get_project(proj["id"])["script"]

    # 注入：第二个镜头插入时失败 → 整个重建（含剧本文本更新）必须回滚
    state = {"shots": 0}
    orig = sb._new_id

    def flaky(prefix: str) -> str:
        if prefix == "sht":
            state["shots"] += 1
            if state["shots"] > 1:
                raise RuntimeError("注入：第二个镜头插入失败")
        return orig(prefix)

    monkeypatch.setattr(sb, "_new_id", flaky)
    with pytest.raises(RuntimeError, match="注入"):
        sb.import_script(proj["id"], SCRIPT_A2, mode="rebuild")
    monkeypatch.setattr(sb, "_new_id", orig)

    assert [s["title"] for s in sb.list_scenes(proj["id"])] == scenes_before, "重建失败却丢了旧场景"
    assert sb.get_project(proj["id"])["script"] == script_before, "重建失败却改了剧本文本（S3）"
    assert sb.list_timeline(proj["id"]) == [], "时间线被误清"


def test_import_rebuild_replaces_atomically(fresh_db):
    proj = sb.create_project("rb2", "")
    sb.import_script(proj["id"], SCRIPT_A, mode="rebuild")
    result = sb.import_script(proj["id"], SCRIPT_A2, mode="rebuild")
    assert result["mode"] == "rebuild"
    assert result["removed"]["scenes"] == 2 and result["removed"]["shots"] == 2
    assert result["scene_count"] == 1  # SCRIPT_A2 只有一个场景标记
    scenes = sb.list_scenes(proj["id"])
    assert [s["title"] for s in scenes] == ["雨夜"]
    assert len(scenes[0]["shots"]) == 2  # 甲独自走在街上 + 乙追上来


def test_import_append_skips_overlapping_titles(fresh_db):
    proj = sb.create_project("ap", "")
    sb.import_script(proj["id"], SCRIPT_A, mode="rebuild")
    result = sb.import_script(proj["id"], SCRIPT_A + "\n场景三：结尾\n众人散去", mode="append")
    titles = [s["title"] for s in sb.list_scenes(proj["id"])]
    assert "教室" in titles and "操场" in titles and "结尾" in titles
    assert titles.count("教室") == 1, "append 模式覆盖了同名旧场景"


def test_insert_and_get_returns_actual_record(fresh_db):
    proj = sb.create_project("ig", "说明")
    assert proj["id"].startswith("prj_") and proj["name"] == "ig" and proj["description"] == "说明"


def test_insert_and_get_failure_raises_and_leaves_no_row(fresh_db):
    with pytest.raises(Exception):
        fresh_db.insert_and_get(
            "projects",
            ["id", "name", "created_at", "updated_at"],
            ("prj_bad", None, "t", "t"),  # name 为 NOT NULL
            "prj_bad",
        )
    assert fresh_db.fetch_one("SELECT * FROM projects WHERE id = ?", ("prj_bad",)) is None
    # 失败后连接无残留事务，后续操作正常
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM projects")["c"] >= 0
    proj = sb.create_project("after-fail", "")
    assert proj and proj["id"]


def test_create_job_returns_record(fresh_db):
    job = fresh_db.create_job({"engine": "anime", "prompt": "p", "width": 512,
                               "height": 512, "num_frames": 16, "fps": 8,
                               "steps": 25, "guidance": 7.5, "seed": 7})
    assert job and job["id"] and job["status"] == "pending" and job["seed"] == 7
