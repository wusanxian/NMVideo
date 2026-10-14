"""SQLite 任务存储（标准库实现，零额外依赖）"""
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DB_PATH


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path = DB_PATH):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    engine TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    negative_prompt TEXT DEFAULT '',
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    num_frames INTEGER NOT NULL,
                    fps INTEGER NOT NULL,
                    steps INTEGER NOT NULL,
                    guidance REAL NOT NULL,
                    seed INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress REAL NOT NULL DEFAULT 0,
                    stage TEXT DEFAULT '',
                    error TEXT,
                    video_url TEXT,
                    poster_url TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            # 轻量迁移：给旧库补新增字段
            existing = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            for col, ddl in (
                ("preset", "TEXT DEFAULT ''"),
                ("preview", "INTEGER DEFAULT 0"),
                ("hardware", "TEXT DEFAULT ''"),
                ("elapsed_s", "REAL DEFAULT 0"),
            ):
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
            self._conn.commit()

    def create_job(self, params: dict) -> dict:
        job_id = uuid.uuid4().hex[:8]
        row = {
            "id": job_id,
            "status": "pending",
            "progress": 0.0,
            "stage": "排队中",
            "created_at": _utcnow(),
            **params,
        }
        keys = list(row.keys())
        sql = f"INSERT INTO jobs ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})"
        with self._lock:
            self._conn.execute(sql, tuple(row.values()))
            self._conn.commit()
        return self.get_job(job_id)

    def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        sql = f"UPDATE jobs SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
        with self._lock:
            self._conn.execute(sql, (*fields.values(), job_id))
            self._conn.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 100, status: Optional[str] = None) -> list:
        sql = "SELECT * FROM jobs"
        args: list = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            cur = self._conn.execute(sql, tuple(args))
            return [dict(r) for r in cur.fetchall()]

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()


db = Database()
