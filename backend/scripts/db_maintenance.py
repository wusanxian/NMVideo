#!/usr/bin python3
"""数据库维护工具：审计 / 一致性备份 / 显式修复孤儿行

用法（在 backend/ 目录下，用项目 venv）：
    python scripts/db_maintenance.py audit            # 只读审计孤儿记录
    python scripts/db_maintenance.py backup [目录]    # 一致性备份（Backup API）
    python scripts/db_maintenance.py repair [--no-backup]  # 先备份再删孤儿行

设计约束（第二阶段 2A）：
  - 启动流程绝不静默删除数据；修复只能通过本工具显式执行。
  - repair 前强制一致性备份，备份文件含 sha256 校验值（.sha256 边车文件）。
  - 修复前后输出所有业务表行数对比，便于核对。
"""
import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 允许直接运行

from app import db as db_module  # noqa: E402
from app.db import _FK_TABLES  # noqa: E402

BIZ_TABLES = [
    "projects", "scenes", "shots", "shot_versions", "timeline_items", "exports",
    "characters", "shot_voices", "audio_tracks", "subtitle_tracks",
    "review_history", "jobs",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _table_counts(conn) -> dict:
    out = {}
    for t in BIZ_TABLES:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            out[t] = -1
    return out


def cmd_audit(db) -> int:
    audit = db.audit_orphans()
    if not audit:
        print("[audit] 未发现孤儿记录，数据库一致。")
        return 0
    total = sum(i["count"] for i in audit)
    print(f"[audit] 发现 {total} 条孤儿记录：")
    for i in audit:
        print(f"  - {i['table']}.{i['column']} -> {i['parent']}: "
              f"{i['count']} 条，样例 {', '.join(i['sample_ids'][:5])}")
    print("[audit] 修复请运行: python scripts/db_maintenance.py repair")
    return 1


def cmd_backup(db, dest_dir) -> int:
    dest_dir = Path(dest_dir)
    dest = db.backup(dest_dir / f"aivideo-backup-manual-{_stamp()}.db")
    digest = _sha256(dest)
    sidecar = dest.with_suffix(dest.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {dest.name}\n", encoding="utf-8")
    print(f"[backup] 已备份 -> {dest}")
    print(f"[backup] sha256 -> {digest}")
    return 0


def _stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def cmd_repair(db, no_backup) -> int:
    audit = db.audit_orphans()
    if not audit:
        print("[repair] 未发现孤儿记录，无需修复。")
        return 0
    before = _table_counts(db._conn)
    result = db.repair_orphans(backup_dir=None if no_backup else "backups")
    after = _table_counts(db._conn)
    if result["backup"]:
        print(f"[repair] 修复前备份 -> {result['backup']}")
    print(f"[repair] 已删除 {result['total']} 条孤儿记录：")
    for k, v in result["removed"].items():
        print(f"  - {k}: {v} 条")
    print("[repair] 表行数对比（修复前 -> 修复后）：")
    for t in BIZ_TABLES:
        mark = "" if before[t] == after[t] else "  <== 变化"
        print(f"  {t}: {before[t]} -> {after[t]}{mark}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="aivideo 数据库维护工具")
    parser.add_argument("command", choices=["audit", "backup", "repair"])
    parser.add_argument("dest", nargs="?", default="backups", help="backup 目标目录")
    parser.add_argument("--no-backup", action="store_true", help="repair 时跳过备份（不推荐）")
    args = parser.parse_args()

    db = db_module.db
    if args.command == "audit":
        return cmd_audit(db)
    if args.command == "backup":
        return cmd_backup(db, args.dest)
    return cmd_repair(db, args.no_backup)


if __name__ == "__main__":
    raise SystemExit(main())
