"""2A 旧库迁移测试：启动只审计不删除、显式修复先备份、备份可恢复。

模拟旧库：外键曾处于关闭状态，子表残留孤儿行。验证：
  1. 启动后外键启用、孤儿被审计出来、但一行都没被删（审查项 D2）；
  2. 正常业务数据在启动前后完全一致（无异常库不被改写）；
  3. repair_orphans 先做一致性备份再删除，备份可完整恢复（含孤儿与正常数据）。
"""
import shutil
import sqlite3

from app.db import Database


def _build_legacy_db(path):
    """造一个带孤儿的旧库：先建全新 schema，再关外键插孤儿行。"""
    db = Database(path)
    db.insert_and_get(
        "projects", ["id", "name", "created_at", "updated_at"],
        ("prj_ok", "正常项目", "t", "t"), "prj_ok",
    )
    db.insert_and_get(
        "scenes", ["id", "project_id", "title", "order_index", "created_at", "updated_at"],
        ("scn_ok", "prj_ok", "正常场景", 0, "t", "t"), "scn_ok",
    )
    db.insert_and_get(
        "shots", ["id", "scene_id", "title", "order_index", "created_at", "updated_at"],
        ("sht_ok", "scn_ok", "正常镜头", 0, "t", "t"), "sht_ok",
    )
    db._conn.close()
    raw = sqlite3.connect(str(path))  # 新连接默认 FK 关闭 = 旧库状态
    raw.execute(
        "INSERT INTO shot_versions (id, shot_id, version_no, created_at)"
        " VALUES ('ver_orphan', 'sht_missing', 1, 't')"
    )
    raw.execute(
        "INSERT INTO scenes (id, project_id, title, order_index, created_at, updated_at)"
        " VALUES ('scn_orphan', 'prj_missing', '孤儿场景', 9, 't', 't')"
    )
    raw.commit()
    raw.close()


def _row_counts(db) -> dict:
    tables = ("projects", "scenes", "shots", "shot_versions")
    return {t: db.fetch_one(f"SELECT COUNT(*) AS c FROM {t}")["c"] for t in tables}


def test_startup_audits_orphans_without_deleting(tmp_path):
    path = tmp_path / "legacy.db"
    _build_legacy_db(path)
    counts_before = None

    db = Database(path)  # 旧库启动
    audit = db.audit_orphans()
    tables = {a["table"] for a in audit}
    assert "shot_versions" in tables and "scenes" in tables, "孤儿未被审计出来"
    assert db.last_orphan_audit, "启动审计结果未记录"

    # 一行都没被静默删除
    assert db.fetch_one("SELECT * FROM shot_versions WHERE id = 'ver_orphan'")
    assert db.fetch_one("SELECT * FROM scenes WHERE id = 'scn_orphan'")
    # 正常数据完好
    assert db.fetch_one("SELECT * FROM shots WHERE id = 'sht_ok'")
    assert db.fetch_one("SELECT * FROM scenes WHERE id = 'scn_ok'")

    # 外键已启用
    fk = db.fetch_all("PRAGMA foreign_keys")
    assert fk and fk[0]["foreign_keys"] == 1

    # 启动未改写业务数据（与裸连接统计一致）
    raw = sqlite3.connect(str(path))
    counts_after = {
        t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("projects", "scenes", "shots", "shot_versions")
    }
    raw.close()
    assert counts_after == {"projects": 1, "scenes": 2, "shots": 1, "shot_versions": 1}
    db._conn.close()


def test_healthy_db_startup_is_noop(tmp_path):
    """无异常库：启动不发生任何业务数据改写。"""
    path = tmp_path / "clean.db"
    db = Database(path)
    db.insert_and_get(
        "projects", ["id", "name", "created_at", "updated_at"],
        ("prj_c", "干净库", "t", "t"), "prj_c",
    )
    before = _row_counts(db)
    db._conn.close()
    db2 = Database(path)
    assert db2.last_orphan_audit == []
    assert _row_counts(db2) == before
    db2._conn.close()


def test_repair_backs_up_then_cleans_and_backup_restores(tmp_path):
    path = tmp_path / "legacy2.db"
    _build_legacy_db(path)
    db = Database(path)
    assert db.audit_orphans()

    result = db.repair_orphans(backup_dir=tmp_path / "backups")
    assert result["total"] >= 2, "孤儿未被清理"
    backup = result["backup"]
    assert backup and __import__("pathlib").Path(backup).exists(), "修复前未备份"
    # 清理后审计干净，正常数据不受影响
    assert db.audit_orphans() == []
    assert db.fetch_one("SELECT * FROM shots WHERE id = 'sht_ok'")
    db._conn.close()

    # 备份可恢复：把备份拷回原路径重新打开，孤儿与正常数据都在
    shutil.copy(backup, path)
    restored = Database(path)
    assert restored.fetch_one("SELECT * FROM shot_versions WHERE id = 'ver_orphan'")
    assert restored.fetch_one("SELECT * FROM scenes WHERE id = 'scn_orphan'")
    assert restored.fetch_one("SELECT * FROM shots WHERE id = 'sht_ok'")
    assert restored.fetch_one("SELECT * FROM scenes WHERE id = 'scn_ok'")
    restored._conn.close()


def test_repair_on_healthy_db_does_nothing(tmp_path):
    path = tmp_path / "clean2.db"
    db = Database(path)
    result = db.repair_orphans(backup_dir=tmp_path / "bk")
    assert result == {"backup": None, "removed": {}, "total": 0}
    db._conn.close()
