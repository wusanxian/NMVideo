"""SQLite 任务存储（标准库实现，零额外依赖）

第二阶段 2A 访问收口：
  - 所有业务访问经 fetch_one / fetch_all / execute / transaction()，
    完整语句（含游标结果读取）与提交/回滚都在同一把锁内，消除
    「读无锁 + 写另锁」的事务边界模糊（审查项 D1）。
  - transaction() 提供 BEGIN IMMEDIATE 单事务；事务内只允许使用 yield
    出的 conn（锁不可重入），供 import_script / 归档登记等多步业务使用。
  - 启动不再静默删除孤儿行（审查项 D2）：改为只读审计 + 显式修复
    （repair_orphans，修复前自动一致性备份）。
  - 忙等策略：timeout=10s，SQLITE_BUSY 翻译为可重试的 DatabaseBusyError。
"""
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DB_PATH


class DatabaseBusyError(RuntimeError):
    """数据库正忙（其他进程持有写锁）。可重试，调用方应稍后重试。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 声明了 REFERENCES ... ON DELETE CASCADE 的表（子表 → 父表列）
_FK_TABLES = [
    ("shot_versions", "shots", "shot_id"),
    ("shot_voices", "shots", "shot_id"),
    ("timeline_items", "shots", "shot_id"),
    ("timeline_items", "projects", "project_id"),
    ("scenes", "projects", "project_id"),
    ("shots", "scenes", "scene_id"),
    ("characters", "projects", "project_id"),
    ("audio_tracks", "projects", "project_id"),
    ("subtitle_tracks", "projects", "project_id"),
    ("exports", "projects", "project_id"),
]


class Database:
    def __init__(self, path: Path = DB_PATH):
        self._path = Path(path)
        # timeout=10s：与维护脚本等外部写者竞争时等待而非立即 SQLITE_BUSY
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.last_orphan_audit: list = []
        self._enable_foreign_keys()
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
                ("prompt_id", "TEXT DEFAULT ''"),        # ComfyUI prompt_id（2B：持久化，重启恢复用）
                ("workflow_snapshot", "TEXT DEFAULT ''"),  # ComfyUI 工作流快照路径（2B：入队即固化）
                ("client_id", "TEXT DEFAULT ''"),        # ComfyUI 提交 client_id（P0-01：与 prompt_id 同时落库，恢复可续 WS）
                ("generation_target", "TEXT DEFAULT '{}'"),
                ("parent_job_id", "TEXT DEFAULT ''"),
                ("preview_kind", "TEXT DEFAULT 'rerun'"),
                ("native_output", "TEXT DEFAULT '{}'"),
                ("generation_inputs", "TEXT DEFAULT '[]'"),
                ("comfy_url", "TEXT DEFAULT ''"),  # 每个任务固定其执行服务，设置切换不迁移旧任务
                ("use_character_images", "INTEGER DEFAULT 1"),
                ("use_character_lora", "INTEGER DEFAULT 1"),
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
                    fps INTEGER NOT NULL DEFAULT 24,
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
                    fps INTEGER NOT NULL DEFAULT 0,
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

        # ---------- 第三批轻量迁移：审核三态 / 剧本导入 revision ----------
        with self._lock:
            proj_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(projects)").fetchall()
            }
            if "revision" not in proj_cols:
                # 剧本导入 revision：预览绑定 + 过期提交拒绝（审查项 S3）
                self._conn.execute("ALTER TABLE projects ADD COLUMN revision INTEGER DEFAULT 0")
            shot_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(shots)").fetchall()
            }
            if "review_stage" not in shot_cols:
                # 审核三态：design（设计审核）/ generation（生成审核）/ video（视频审核）/ ''（无待审）
                self._conn.execute("ALTER TABLE shots ADD COLUMN review_stage TEXT DEFAULT ''")
            rvh_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(review_history)").fetchall()
            }
            if "stage" not in rvh_cols:
                # 审核记录所属审核门（design/generation/video）
                self._conn.execute("ALTER TABLE review_history ADD COLUMN stage TEXT DEFAULT ''")
            self._conn.commit()

        # ---------- 第四批轻量迁移：首尾帧独立输入 / 续接来源版本（PR-02） ----------
        with self._lock:
            shot_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(shots)").fetchall()
            }
            for col, ddl in (
                ("first_frame_image", "TEXT DEFAULT ''"),   # 独立首帧图 URL（旧 reference_image 兼容：未设时充当首帧）
                ("last_frame_image", "TEXT DEFAULT ''"),    # 独立尾帧图 URL（first_last 模式必填）
                ("source_video_version_id", "TEXT DEFAULT ''"),  # 续接抽帧来源版本（可复现 + 下游过期提示）
            ):
                if col not in shot_cols:
                    self._conn.execute(f"ALTER TABLE shots ADD COLUMN {col} {ddl}")
            job_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            # 入队即冻结的输入资产快照（可复现：版本 params 记录当时首/尾帧与来源版本）
            for col, ddl in (
                ("first_frame_image", "TEXT DEFAULT ''"),
                ("last_frame_image", "TEXT DEFAULT ''"),
                ("source_video_version_id", "TEXT DEFAULT ''"),
            ):
                if col not in job_cols:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
            self._conn.commit()

        # ---------- 第五批轻量迁移：角色快照冻结（PR-04 导演工作台） ----------
        with self._lock:
            # 入队时冻结生成所用角色卡（外貌/服装/LoRA/参考图/音色）：
            # job 带快照入队，完成后落进 shot_versions——之后改角色卡
            # 不影响已生成版本的复现语义（镜头锁定角色版本）
            for table in ("jobs", "shot_versions"):
                cols = {
                    r["name"] for r in self._conn.execute(
                        f"PRAGMA table_info({table})").fetchall()
                }
                if "characters_snapshot" not in cols:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN characters_snapshot TEXT DEFAULT '[]'")
            self._conn.commit()

        # ---------- 第六批轻量迁移：导出帧率参数化（PR-06） ----------
        with self._lock:
            proj_cols2 = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(projects)").fetchall()
            }
            # 项目级帧率（旧库默认 24，与主流视频帧率一致；历史行为 concat 固定 16）
            if "fps" not in proj_cols2:
                self._conn.execute("ALTER TABLE projects ADD COLUMN fps INTEGER DEFAULT 24")
            exp_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(exports)").fetchall()
            }
            # 导出记录冻结当时所选帧率（0 = 旧记录，运行时回落项目帧率再回落 24）
            if "fps" not in exp_cols:
                self._conn.execute("ALTER TABLE exports ADD COLUMN fps INTEGER DEFAULT 0")
            if "manifest" not in exp_cols:
                self._conn.execute("ALTER TABLE exports ADD COLUMN manifest TEXT DEFAULT ''")
            self._conn.commit()

        # ---------- 启动审计（只读）：旧库孤儿行只报告，不静默删除 ----------
        try:
            self.last_orphan_audit = self.audit_orphans()
            self._log_orphan_audit(self.last_orphan_audit)
        except Exception as e:  # 审计失败不影响启动
            print(f"[db] 孤儿记录审计失败（不影响启动）: {e}")

    @staticmethod
    def _log_orphan_audit(audit: list) -> None:
        if not audit:
            return
        total = sum(item["count"] for item in audit)
        print(f"[db] 审计到 {total} 条孤儿记录（未自动删除）。"
              f"修复请运行: python scripts/db_maintenance.py repair（修复前自动备份）")
        for item in audit:
            print(f"[db]   - {item['table']}.{item['column']} -> {item['parent']}:"
                  f" {item['count']} 条，样例 {', '.join(item['sample_ids'][:3])}")

    def _enable_foreign_keys(self):
        """启用 SQLite 外键约束（PRAGMA foreign_keys 为连接级开关，默认关闭）。

        注意（审查项 D2）：旧库可能残留孤儿行（父表已删、子表残留）。
        启动时**只启用约束、只审计孤儿，绝不静默删除**——审计结果存
        self.last_orphan_audit 并打印告警；修复必须走显式操作
        （repair_orphans() 或 scripts/db_maintenance.py repair），
        且修复前自动做一致性备份。无异常的库启动不发生任何数据改写。
        """
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")

    # ---------- 2A：统一访问接口（锁覆盖完整语句与结果读取） ----------
    def _busy_or_raise(self, exc: sqlite3.Error) -> Exception:
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            return DatabaseBusyError(
                "数据库正忙（其他进程正在写入），请稍后重试；"
                "若持续出现请检查是否有外部程序正在写同一数据库文件"
            )
        return exc

    def _safe_rollback(self) -> None:
        try:
            if self._conn.in_transaction:
                self._conn.rollback()
        except sqlite3.Error:
            pass

    def fetch_one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        with self._lock:
            try:
                row = self._conn.execute(sql, tuple(args)).fetchone()
            except sqlite3.OperationalError as e:
                self._safe_rollback()
                raise self._busy_or_raise(e)
        return dict(row) if row else None

    def fetch_all(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            try:
                rows = self._conn.execute(sql, tuple(args)).fetchall()
            except sqlite3.OperationalError as e:
                self._safe_rollback()
                raise self._busy_or_raise(e)
        return [dict(r) for r in rows]

    def execute(self, sql: str, args: tuple = ()) -> None:
        """单句写 + commit。多步业务请用 transaction()。"""
        with self._lock:
            try:
                self._conn.execute(sql, tuple(args))
                self._conn.commit()
            except sqlite3.OperationalError as e:
                self._safe_rollback()
                raise self._busy_or_raise(e)

    @contextmanager
    def transaction(self):
        """业务事务：BEGIN IMMEDIATE 单写锁；异常整体回滚，成功才提交。

        约束：事务体内只允许使用 yield 出的 conn 执行语句（含读取），
        不得调用本对象其他方法——锁不可重入，否则死锁。
        """
        with self._lock:
            conn = self._conn
            if conn.in_transaction:  # 防御：清掉残留的隐式事务
                conn.rollback()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise
            conn.commit()

    def insert_and_get(self, table: str, columns: list, values: tuple,
                       row_id: str) -> dict:
        """INSERT + 同一事务内按 id 读回。

        保证「创建成功必返回实际记录」：读回与插入之间不会被其他请求
        删掉（审查项 D1 的「创建接口返回 null」）。读不到直接抛异常，
        不用空对象掩盖失败。
        """
        sql = (f"INSERT INTO {table} ({','.join(columns)})"
               f" VALUES ({','.join('?' * len(values))})")
        with self.transaction() as conn:
            try:
                conn.execute(sql, tuple(values))
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE id = ?", (row_id,)
                ).fetchone()
            except sqlite3.OperationalError as e:
                raise self._busy_or_raise(e)
        if not row:
            raise RuntimeError(f"插入后读回失败: {table} id={row_id}")
        return dict(row)

    # ---------- 备份 / 审计 / 显式修复 ----------
    def backup(self, dest) -> Path:
        """SQLite 一致性备份（Backup API，与运行中的写入并存，见 SQLite 备份文档）"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dst = sqlite3.connect(str(dest))
            try:
                self._conn.backup(dst)
            finally:
                dst.close()
        return dest

    def auto_backup(self, prefix: str = "aivideo-backup") -> Path:
        """按默认约定做一致性备份：库同级 backups/ 目录 + UTC 时间戳命名。

        供破坏性操作前的自动备份使用（调用方无需自己拼路径）。
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = self._path.parent / "backups" / f"{prefix}-{stamp}.db"
        return self.backup(dest)

    def audit_orphans(self) -> list:
        """只读审计：返回孤儿记录清单（不写库）。

        [{table, parent, column, count, sample_ids}]，空列表表示无异常。
        """
        with self._lock:
            return self._audit_orphans_locked()

    def _audit_orphans_locked(self) -> list:
        out = []
        for table, parent, col in _FK_TABLES:
            try:
                rows = self._conn.execute(
                    f"SELECT id FROM {table} WHERE {col} != '' AND {col} IS NOT NULL"
                    f" AND NOT EXISTS (SELECT 1 FROM {parent} WHERE {parent}.id = {table}.{col})"
                ).fetchall()
            except sqlite3.OperationalError:
                continue  # 表还没建（首次启动）
            if rows:
                out.append({
                    "table": table, "parent": parent, "column": col,
                    "count": len(rows), "sample_ids": [r["id"] for r in rows[:5]],
                })
        return out

    def repair_orphans(self, backup_dir=None) -> dict:
        """显式修复孤儿行：先一致性备份，再按审计清单删除。

        返回 {backup, removed, total}；无孤儿时不做任何改写。
        """
        audit = self.audit_orphans()
        if not audit:
            return {"backup": None, "removed": {}, "total": 0}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target_dir = Path(backup_dir) if backup_dir else self._path.parent / "backups"
        dest = target_dir / f"aivideo-pre-repair-{stamp}.db"
        self.backup(dest)
        removed = {}
        with self._lock:
            for item in audit:
                cur = self._conn.execute(
                    f"DELETE FROM {item['table']} WHERE {item['column']} != ''"
                    f" AND {item['column']} IS NOT NULL AND NOT EXISTS"
                    f" (SELECT 1 FROM {item['parent']} WHERE {item['parent']}.id = {item['table']}.{item['column']})"
                )
                removed[f"{item['table']}.{item['column']}->{item['parent']}"] = cur.rowcount
            self._conn.commit()
        return {"backup": str(dest), "removed": removed, "total": sum(removed.values())}

    def create_job(self, params: dict, *, job_id: str = None) -> dict:
        job_id = job_id or uuid.uuid4().hex[:8]
        row = {
            "id": job_id,
            "status": "pending",
            "progress": 0.0,
            "stage": "排队中",
            "created_at": _utcnow(),
            **params,
        }
        return self.insert_and_get("jobs", list(row.keys()), tuple(row.values()), job_id)

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

    def claim_job(self, job_id: str) -> bool:
        """与取消互斥认领；已取消的排队记录不可复活。running 用于重启续跑。"""
        with self.transaction() as conn:
            result = conn.execute(
                "UPDATE jobs SET status='running', stage='准备中', progress=0"
                " WHERE id=? AND status IN ('pending', 'running')", (job_id,),
            )
            return result.rowcount == 1

    def cancel_job(self, job_id: str, *, note: str = "已取消",
                   statuses=("pending", "running", "needs_attention")) -> bool:
        """同一事务取消任务并解除它仍持有的镜头；旧任务不能释放新任务的占用。"""
        with self.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job or job["status"] not in statuses:
                return False
            now = _utcnow()
            conn.execute(
                "UPDATE jobs SET status='cancelled', stage='已取消', finished_at=? WHERE id=?",
                (now, job_id),
            )
            if job["shot_id"]:
                conn.execute(
                    "UPDATE shots SET job_id='', status='draft', review_stage='', note=?, updated_at=?"
                    " WHERE id=? AND job_id=?",
                    (note, now, job["shot_id"], job_id),
                )
        return True

    def list_jobs(self, limit: int = 100, status: Optional[str] = None,
                  order: str = "desc", offset: int = 0, after: Optional[tuple] = None) -> list:
        sql = "SELECT * FROM jobs"
        args: list = []
        conditions = []
        if status:
            conditions.append("status = ?")
            args.append(status)
        direction = "ASC" if order == "asc" else "DESC"
        if after is not None:
            operator = ">" if order == "asc" else "<"
            conditions.append(f"(created_at, id) {operator} (?, ?)")
            args.extend(after)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        # 唯一 id 消除同一秒内的排序歧义；恢复时使用游标，状态变化不会跳行。
        sql += f" ORDER BY created_at {direction}, id {direction} LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            cur = self._conn.execute(sql, tuple(args))
            return [dict(r) for r in cur.fetchall()]

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()


db = Database()
