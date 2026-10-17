"""2A 并发与隔离测试（审查项 D1：共享连接 + 读无锁 + 写另锁）。

用可控线程同步点复现交错（屏障同跑、事务挂起时读者阻塞），
不是随机压测；断言：无意外异常、创建不返回 null、写入数量准确、无串扰。
"""
import threading
import time

from app import assets, storyboard as sb, tracks


def _job_params(seed: int) -> dict:
    return {
        "engine": "anime", "prompt": "test prompt", "negative_prompt": "",
        "width": 512, "height": 512, "num_frames": 16, "fps": 8,
        "steps": 25, "guidance": 7.5, "seed": seed,
    }


def test_concurrent_mixed_operations(fresh_db):
    """10 个线程 × 每线程 20 轮混合请求：项目创建/读取/更新 + 任务写入 + 资产/音轨。"""
    n_threads, ops = 10, 20
    barrier = threading.Barrier(n_threads)
    errors: list = []
    created = {"projects": [], "jobs": [], "characters": []}
    guard = threading.Lock()

    def worker(tid: int):
        try:
            barrier.wait(timeout=30)
            for i in range(ops):
                proj = sb.create_project(f"t{tid}-p{i}", "desc")
                assert proj and proj.get("id"), "创建项目返回了空对象"
                assert proj["name"] == f"t{tid}-p{i}", "创建读回的字段被串扰"
                with guard:
                    created["projects"].append(proj["id"])
                sb.update_project(proj["id"], description="d2")
                got = sb.get_project(proj["id"])
                assert got and got["description"] == "d2", "更新后读回不一致"
                assert isinstance(sb.list_projects(), list)
                job = fresh_db.create_job(_job_params(tid * 1000 + i))
                assert job and job.get("id"), "创建任务返回了空对象"
                assert job["status"] == "pending"
                with guard:
                    created["jobs"].append(job["id"])
                fresh_db.update_job(job["id"], progress=0.5, stage="采样中")
                assert fresh_db.get_job(job["id"])["progress"] == 0.5, "任务进度写入丢失"
                char = assets.create_character(proj["id"], name=f"c{tid}-{i}")
                assert char and char.get("id"), "创建角色返回了空对象"
                with guard:
                    created["characters"].append(char["id"])
                assets.list_characters(proj["id"])
                tracks.create_audio_track(proj["id"], title="bgm", audio_url="/outputs/x.mp3")
                tracks.list_audio_tracks(proj["id"])
        except Exception as e:  # noqa: BLE001
            errors.append((tid, repr(e)))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    assert not errors, f"并发出现异常（前 3 条）: {errors[:3]}"

    expect = n_threads * ops
    assert len(created["projects"]) == expect, "项目写入数量不符"
    assert len(set(created["projects"])) == expect, "出现重复项目 id"
    assert len(created["jobs"]) == expect, "任务写入数量不符"
    assert len(set(created["jobs"])) == expect, "出现重复任务 id"
    assert len(created["characters"]) == expect, "角色写入数量不符"
    # 数据库实际行数与内存记录一致
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM projects")["c"] == expect
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM jobs")["c"] == expect
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM characters")["c"] == expect


def test_reader_never_sees_half_committed_transaction(fresh_db):
    """事务挂起期间，读者必须阻塞等待，不能看到半完成数据。"""
    proj = sb.create_project("iso", "")
    started = threading.Event()
    release = threading.Event()
    seen: dict = {}

    def writer():
        with fresh_db.transaction() as conn:
            conn.execute(
                "INSERT INTO scenes (id, project_id, title, order_index,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("scn_iso", proj["id"], "事务进行中", 0, "t", "t"),
            )
            started.set()
            release.wait(timeout=10)

    def reader():
        seen["count"] = len(sb.list_scenes(proj["id"]))

    t1 = threading.Thread(target=writer)
    t1.start()
    assert started.wait(timeout=5), "写线程未能进入事务"
    t2 = threading.Thread(target=reader)
    t2.start()
    time.sleep(0.4)  # 读者此刻只可能阻塞在锁上，不可能读到未提交数据
    assert seen.get("count") != 1, "读者在事务提交前看到了半完成数据"
    release.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert seen.get("count") == 1, "提交后读者应看到新场景"


def test_transaction_rollback_leaves_no_trace(fresh_db):
    """事务内异常 → 整体回滚，外部读者看不到一半的数据。"""
    proj = sb.create_project("rb", "")
    sb.create_scene(proj["id"], "旧场景")
    try:
        with fresh_db.transaction() as conn:
            conn.execute(
                "INSERT INTO scenes (id, project_id, title, order_index,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("scn_new", proj["id"], "新场景", 1, "t", "t"),
            )
            raise RuntimeError("注入异常")
    except RuntimeError:
        pass
    scenes = sb.list_scenes(proj["id"])
    assert [s["title"] for s in scenes] == ["旧场景"]
    # 回滚后连接仍可正常使用（无残留事务）
    assert fresh_db.fetch_one("SELECT COUNT(*) AS c FROM scenes")["c"] == 1
