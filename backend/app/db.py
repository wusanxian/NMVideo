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
                ("shot_id", "TEXT DEFAULT ''"),   # 关联分镜（分镜工作台提交的生成任务）
                ("reference_image", "TEXT DEFAULT ''"),  # 参考图/首帧（i2v/首尾帧/续接）
                ("mode", "TEXT DEFAULT 't2v'"),          # t2v/i2v/first_last/continuation
                ("continue_from", "TEXT DEFAULT ''"),    # 续接来源镜头
            ):
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
            self._conn.commit()

        # ---------- 分镜工作台：Project / Scene / Shot 三层 ----------
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    script TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scenes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    environment TEXT DEFAULT '',
                    lighting TEXT DEFAULT '',
                    order_index INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shots (
                    id TEXT PRIMARY KEY,
                    scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
                    title TEXT DEFAULT '',
                    dialogue TEXT DEFAULT '',
                    shot_size TEXT DEFAULT '中景',
                    camera_movement TEXT DEFAULT '固定',
                    duration_s REAL NOT NULL DEFAULT 3,
                    prompt TEXT DEFAULT '',
                    negative_prompt TEXT DEFAULT '',
                    reference_image TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    order_index INTEGER NOT NULL DEFAULT 0,
                    job_id TEXT DEFAULT '',
                    current_version_id TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shot_versions (
                    id TEXT PRIMARY KEY,
                    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
                    job_id TEXT DEFAULT '',
                    version_no INTEGER NOT NULL DEFAULT 1,
                    prompt TEXT DEFAULT '',
                    negative_prompt TEXT DEFAULT '',
                    engine TEXT DEFAULT '',
                    params TEXT DEFAULT '{}',
                    video_url TEXT DEFAULT '',
                    poster_url TEXT DEFAULT '',
                    seed INTEGER DEFAULT -1,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS timeline_items (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    trim_in_s REAL NOT NULL DEFAULT 0,
                    trim_out_s REAL NOT NULL DEFAULT 0,
                    transition TEXT NOT NULL DEFAULT 'none',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress REAL NOT NULL DEFAULT 0,
                    stage TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    file_url TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    role TEXT DEFAULT '配角',
                    appearance TEXT DEFAULT '',
                    outfit TEXT DEFAULT '',
                    voice_id TEXT DEFAULT '',
                    voice_name TEXT DEFAULT '',
                    lora TEXT DEFAULT '',
                    expressions TEXT DEFAULT '[]',
                    reference_images TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shot_voices (
                    id TEXT PRIMARY KEY,
                    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
                    character_id TEXT DEFAULT '',
                    character_name TEXT DEFAULT '',
                    voice TEXT DEFAULT '',
                    text TEXT DEFAULT '',
                    audio_url TEXT DEFAULT '',
                    duration_s REAL DEFAULT 0,
                    engine TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'done',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audio_tracks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL DEFAULT 'music',
                    title TEXT DEFAULT '',
                    audio_url TEXT DEFAULT '',
                    start_s REAL NOT NULL DEFAULT 0,
                    trim_in_s REAL NOT NULL DEFAULT 0,
                    trim_out_s REAL NOT NULL DEFAULT 0,
                    volume REAL NOT NULL DEFAULT 0.8,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subtitle_tracks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    shot_id TEXT DEFAULT '',
                    text TEXT DEFAULT '',
                    start_s REAL NOT NULL DEFAULT 0,
                    end_s REAL NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_history (
                    id TEXT PRIMARY KEY,
                    shot_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    from_status TEXT DEFAULT '',
                    to_status TEXT DEFAULT '',
                    version_id TEXT DEFAULT '',
                    comment TEXT DEFAULT '',
                    checks TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

        # ---------- 第二批轻量迁移：角色资产 / 续接 / 场景资产 / 多轨 ----------
        with self._lock:
            shot_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(shots)").fetchall()
            }
            for col, ddl in (
                ("character_ids", "TEXT DEFAULT '[]'"),   # 关联角色（JSON id 列表）
                ("mode", "TEXT DEFAULT 't2v'"),           # t2v / i2v / first_last / continuation
                ("continue_from", "TEXT DEFAULT ''"),     # 续接来源镜头 id
            ):
                if col not in shot_cols:
                    self._conn.execute(f"ALTER TABLE shots ADD COLUMN {col} {ddl}")
            scene_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(scenes)").fetchall()
            }
            for col, ddl in (
                ("layout", "TEXT DEFAULT ''"),
                ("style", "TEXT DEFAULT ''"),
                ("reference_image", "TEXT DEFAULT ''"),
            ):
                if col not in scene_cols:
                    self._conn.execute(f"ALTER TABLE scenes ADD COLUMN {col} {ddl}")
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
