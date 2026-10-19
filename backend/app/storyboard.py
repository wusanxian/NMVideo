"""分镜工作台数据层：Project / Scene / Shot 三层 + 版本 + 时间线 + 导出记录

第二阶段 2A：不再直接触摸 db._conn/db._lock，全部经 db 统一接口
（fetch_one/fetch_all/execute/transaction）；多步业务走同一事务。
"""
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _row(sql: str, args: tuple = ()) -> Optional[dict]:
    return db.fetch_one(sql, args)


def _rows(sql: str, args: tuple = ()) -> list:
    return db.fetch_all(sql, args)


def _write(sql: str, args: tuple = ()):
    db.execute(sql, args)


# =====================================================================
# 3A 数据保护异常（携带引用/差异明细，由路由层映射 409/400）
# =====================================================================
class StalePreviewError(ValueError):
    """预览后项目已被修改，提交过期（S3）"""


class DestructiveImportError(ValueError):
    """破坏性重建未确认：携带将损失的成果明细"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.details = details or {}


class ReferenceConflictError(ValueError):
    """删除/改指针会破坏被引用成果：携带引用明细"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.details = details or {}


class StaleSubmissionError(ValueError):
    """审核提交时版本已变化（过期提交）"""


class ReviewBlockedError(ValueError):
    """审核被质检阻断：携带完整检查结果与评分（422）"""

    def __init__(self, message: str, issues: list = None, score: dict = None):
        super().__init__(message)
        self.issues = issues or []
        self.score = score or {}


# =====================================================================
# 审核三态（第三批：设计 / 生成 / 视频审核分离）
#   design     设计审核（生成前：提示词/对白/时长/角色等创作参数）
#   generation 生成审核（排队/生成中：提交前校验与产出等待）
#   video      视频审核（生成后：成片质检与采用版本）
#   ''         无待审事项
# 旧的 status 状态机（draft/queued/generating/review/approved/rework）不变，
# review_stage 只表达"当前处于哪个审核门"，旧客户端忽略即可。
# =====================================================================
REVIEW_STAGES = ("design", "generation", "video", "")
REVIEW_STAGE_LABELS = {
    "design": "设计审核",
    "generation": "生成审核",
    "video": "视频审核",
    "": "无待审",
}


# =====================================================================
# 镜头状态机
#   draft(草稿) -> queued(排队) -> generating(生成中) -> review(待审核)
#   review -> approved(通过) / rework(需重做) -> draft
# 生成失败自动回 draft；取消回 draft。
# =====================================================================
SHOT_STATUSES = ("draft", "queued", "generating", "review", "approved", "rework")

SHOT_STATUS_LABELS = {
    "draft": "草稿",
    "queued": "排队中",
    "generating": "生成中",
    "review": "待审核",
    "approved": "已通过",
    "rework": "需重做",
}

# 可发起生成的状态（rework 与 approved 允许重做覆盖，生成成功后回到 review）
GENERATABLE = ("draft", "rework", "approved")


# =====================================================================
# Project
# =====================================================================
def create_project(name: str, description: str = "") -> dict:
    pid = _new_id("prj")
    now = _utcnow()
    return db.insert_and_get(
        "projects",
        ["id", "name", "description", "created_at", "updated_at"],
        (pid, name.strip() or "未命名项目", description.strip(), now, now),
        pid,
    )


def list_projects() -> list:
    """项目列表（带场景/分镜统计）"""
    projects = _rows("SELECT * FROM projects ORDER BY updated_at DESC")
    for p in projects:
        p["scene_count"] = _row(
            "SELECT COUNT(*) AS c FROM scenes WHERE project_id = ?", (p["id"],)
        )["c"]
        p["shot_count"] = _row(
            "SELECT COUNT(*) AS c FROM shots s JOIN scenes sc ON s.scene_id = sc.id"
            " WHERE sc.project_id = ?",
            (p["id"],),
        )["c"]
        p["approved_count"] = _row(
            "SELECT COUNT(*) AS c FROM shots s JOIN scenes sc ON s.scene_id = sc.id"
            " WHERE sc.project_id = ? AND s.status = 'approved'",
            (p["id"],),
        )["c"]
    return projects


def get_project(project_id: str) -> Optional[dict]:
    return _row("SELECT * FROM projects WHERE id = ?", (project_id,))


def update_project(project_id: str, **fields) -> Optional[dict]:
    allowed = {"name", "description", "script", "status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return get_project(project_id)
    fields["updated_at"] = _utcnow()
    sql = f"UPDATE projects SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), project_id))
    return get_project(project_id)


def delete_project(project_id: str) -> None:
    # 3A：项目被时间线片段/进行中任务引用时拒绝删除（409 + 明细）
    _guard_no_references(project_references(project_id), "该项目")
    _write("DELETE FROM projects WHERE id = ?", (project_id,))


# =====================================================================
# Scene
# =====================================================================
def create_scene(project_id: str, title: str, description: str = "",
                 environment: str = "", lighting: str = "") -> dict:
    sid = _new_id("scn")
    now = _utcnow()
    max_order = _row(
        "SELECT COALESCE(MAX(order_index), -1) AS m FROM scenes WHERE project_id = ?",
        (project_id,),
    )["m"]
    scene = db.insert_and_get(
        "scenes",
        ["id", "project_id", "title", "description", "environment",
         "lighting", "order_index", "created_at", "updated_at"],
        (sid, project_id, title.strip() or "新场景", description, environment,
         lighting, max_order + 1, now, now),
        sid,
    )
    _touch_project(project_id)
    return scene


def get_scene(scene_id: str) -> Optional[dict]:
    return _row("SELECT * FROM scenes WHERE id = ?", (scene_id,))


def list_scenes(project_id: str) -> list:
    """场景列表（每个场景带分镜数组）"""
    scenes = _rows(
        "SELECT * FROM scenes WHERE project_id = ? ORDER BY order_index, created_at",
        (project_id,),
    )
    for sc in scenes:
        sc["shots"] = _rows(
            "SELECT * FROM shots WHERE scene_id = ? ORDER BY order_index, created_at",
            (sc["id"],),
        )
        for shot in sc["shots"]:
            _decode_shot(shot)
    return scenes


def update_scene(scene_id: str, **fields) -> Optional[dict]:
    allowed = {
        "title", "description", "environment", "lighting", "order_index",
        "layout", "style", "reference_image",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return get_scene(scene_id)
    fields["updated_at"] = _utcnow()
    sql = f"UPDATE scenes SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), scene_id))
    sc = get_scene(scene_id)
    if sc:
        _touch_project(sc["project_id"])
    return sc


def delete_scene(scene_id: str) -> None:
    sc = get_scene(scene_id)
    # 3A：场景内镜头被时间线/任务引用时拒绝删除（级联会毁掉剪辑素材）
    _guard_no_references(
        scene_references(scene_id), f"场景「{sc['title'] if sc else scene_id}」"
    )
    _write("DELETE FROM scenes WHERE id = ?", (scene_id,))
    if sc:
        _touch_project(sc["project_id"])


# =====================================================================
# Shot
# =====================================================================
def create_shot(scene_id: str, **fields) -> dict:
    sid = _new_id("sht")
    now = _utcnow()
    max_order = _row(
        "SELECT COALESCE(MAX(order_index), -1) AS m FROM shots WHERE scene_id = ?",
        (scene_id,),
    )["m"]
    shot = db.insert_and_get(
        "shots",
        ["id", "scene_id", "title", "dialogue", "shot_size", "camera_movement",
         "duration_s", "prompt", "negative_prompt", "reference_image",
         "character_ids", "mode", "continue_from", "order_index",
         "created_at", "updated_at"],
        (sid, scene_id, fields.get("title", ""),
         fields.get("dialogue", ""), fields.get("shot_size", "中景"),
         fields.get("camera_movement", "固定"), float(fields.get("duration_s", 3)),
         fields.get("prompt", ""), fields.get("negative_prompt", ""),
         fields.get("reference_image", ""), json.dumps(fields.get("character_ids") or []),
         fields.get("mode", "t2v"), fields.get("continue_from", ""),
         fields.get("order_index", max_order + 1), now, now),
        sid,
    )
    sc = get_scene(scene_id)
    if sc:
        _touch_project(sc["project_id"])
    return shot


def get_shot(shot_id: str) -> Optional[dict]:
    shot = _row("SELECT * FROM shots WHERE id = ?", (shot_id,))
    if shot:
        shot["versions"] = list_versions(shot_id)
        shot["current_version"] = (
            next((v for v in shot["versions"] if v["id"] == shot["current_version_id"]), None)
        )
        _decode_shot(shot)
        _attach_inheritance_staleness(shot)
    return shot


def _attach_inheritance_staleness(shot: dict) -> None:
    """续接镜头的「继承帧过期」标记（PR-02/PR-08 验收：修改前一镜头后提示下游）。

    续接镜头记录了抽帧来源版本 source_video_version_id；若来源镜头的
    当前采用版本已不是它（上游重生成了），标记 inheritance_stale=True。
    查询失败不打断读取（标记只是提示，不是数据）。
    """
    shot["inheritance_stale"] = False
    src_ver_id = (shot.get("source_video_version_id") or "").strip()
    if (shot.get("mode") or "") != "continuation" or not src_ver_id:
        return
    try:
        src_ver = get_version(src_ver_id)
        if not src_ver:
            shot["inheritance_stale"] = True  # 来源版本已被删除
            return
        src_shot = get_shot_basic(src_ver["shot_id"])
        if src_shot and (src_shot.get("current_version_id") or "") != src_ver_id:
            shot["inheritance_stale"] = True
    except Exception as e:
        print(f"[storyboard] 续接过期检查失败（忽略）: {e}")


def get_shot_basic(shot_id: str) -> Optional[dict]:
    """不带版本列表/角色/过期标记的轻量读取（过期检查内部用，避免递归）。"""
    return _row("SELECT * FROM shots WHERE id = ?", (shot_id,))


def list_shots(scene_id: str) -> list:
    return _rows(
        "SELECT * FROM shots WHERE scene_id = ? ORDER BY order_index, created_at",
        (scene_id,),
    )


def update_shot(shot_id: str, **fields) -> Optional[dict]:
    allowed = {
        "title", "dialogue", "shot_size", "camera_movement", "duration_s",
        "prompt", "negative_prompt", "reference_image", "status", "order_index",
        "job_id", "current_version_id", "note", "scene_id",
        "character_ids", "mode", "continue_from", "review_stage",
        # PR-02：独立首/尾帧与续接来源版本（URL 字符串，兼容旧 reference_image）
        "first_frame_image", "last_frame_image", "source_video_version_id",
    }
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "character_ids" in fields and not isinstance(fields["character_ids"], str):
        fields["character_ids"] = json.dumps(fields["character_ids"])
    if not fields:
        return get_shot(shot_id)
    fields["updated_at"] = _utcnow()
    sql = f"UPDATE shots SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), shot_id))
    return get_shot(shot_id)


def delete_shot(shot_id: str) -> None:
    # 3A：镜头被时间线片段/进行中任务引用时拒绝删除
    _guard_no_references(shot_references(shot_id), "该镜头")
    _write("DELETE FROM shots WHERE id = ?", (shot_id,))
    _write("DELETE FROM timeline_items WHERE shot_id = ?", (shot_id,))


def reorder_shots(scene_id: str, shot_ids: list) -> None:
    """按传入顺序重排分镜 order_index"""
    for i, sid in enumerate(shot_ids):
        _write("UPDATE shots SET order_index = ? WHERE id = ?", (i, sid))
    _touch_scene_project(scene_id)


def set_shot_status(shot_id: str, status: str, **extra) -> None:
    if status not in SHOT_STATUSES:
        raise ValueError(f"未知镜头状态: {status}")
    fields = {"status": status, **extra}
    fields["updated_at"] = _utcnow()
    sql = f"UPDATE shots SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), shot_id))


def _decode_shot(shot: dict) -> None:
    """把 JSON 字段解码为列表；附关联角色与配音摘要"""
    try:
        shot["character_ids"] = json.loads(shot.get("character_ids") or "[]")
    except (ValueError, TypeError):
        shot["character_ids"] = []
    from . import assets, tracks

    shot["characters"] = assets.characters_of_shot(shot)
    shot["voices"] = tracks.list_voices_of_shot(shot["id"])


# =====================================================================
# 引用检查（3A：被引用产物不可误删、采用指针不可静默更换）
# =====================================================================
def shot_references(shot_id: str) -> dict:
    """镜头被哪些成果引用：时间线片段 + 进行中的生成任务"""
    items = _rows(
        "SELECT id, project_id, position FROM timeline_items WHERE shot_id = ?"
        " ORDER BY position", (shot_id,),
    )
    jobs = _rows(
        "SELECT id, status, stage FROM jobs WHERE shot_id = ?"
        " AND status IN ('pending', 'running')", (shot_id,),
    )
    return {"timeline_items": items, "active_jobs": jobs}


def scene_references(scene_id: str) -> dict:
    """场景下所有镜头的引用汇总（含镜头标题，便于定位）"""
    shots = list_shots(scene_id)
    timeline_items, active_jobs = [], []
    for shot in shots:
        refs = shot_references(shot["id"])
        for it in refs["timeline_items"]:
            it["shot_id"] = shot["id"]
            it["shot_title"] = shot.get("title") or ""
            timeline_items.append(it)
        for j in refs["active_jobs"]:
            j["shot_id"] = shot["id"]
            j["shot_title"] = shot.get("title") or ""
            active_jobs.append(j)
    return {
        "shot_count": len(shots),
        "timeline_items": timeline_items,
        "active_jobs": active_jobs,
    }


def project_references(project_id: str) -> dict:
    timeline_items = _rows(
        "SELECT id, shot_id, position FROM timeline_items WHERE project_id = ?"
        " ORDER BY position", (project_id,),
    )
    active_jobs = _rows(
        "SELECT j.id, j.status, j.shot_id FROM jobs j"
        " JOIN shots s ON j.shot_id = s.id JOIN scenes sc ON s.scene_id = sc.id"
        " WHERE sc.project_id = ? AND j.status IN ('pending', 'running')",
        (project_id,),
    )
    return {"timeline_items": timeline_items, "active_jobs": active_jobs}


def job_references(job_id: str) -> dict:
    """生成任务被哪些成果引用：由它产生的镜头版本 + 被时间线引用的版本"""
    versions = _rows(
        "SELECT id, shot_id, version_no, video_url FROM shot_versions WHERE job_id = ?",
        (job_id,),
    )
    timeline_items: list = []
    for v in versions:
        items = _rows(
            "SELECT t.id, t.position, s.title AS shot_title FROM timeline_items t"
            " JOIN shots s ON t.shot_id = s.id WHERE s.current_version_id = ?",
            (v["id"],),
        )
        for it in items:
            it["version_id"] = v["id"]
        timeline_items.extend(items)
    return {"versions": versions, "timeline_items": timeline_items}


def _guard_no_references(refs: dict, what: str) -> None:
    """有引用即拒绝（3A 过渡策略：引用则 409 + 明细，不做静默清理）"""
    if refs["timeline_items"] or refs["active_jobs"]:
        parts = []
        if refs["timeline_items"]:
            positions = ", ".join(
                f"#{it['position'] + 1}" + (f"（{it['shot_title']}）" if it.get("shot_title") else "")
                for it in refs["timeline_items"][:10]
            )
            parts.append(f"时间线片段 {positions}")
        if refs["active_jobs"]:
            parts.append(f"进行中的任务 {len(refs['active_jobs'])} 个")
        raise ReferenceConflictError(
            f"{what}正被{'；'.join(parts)}引用，不能删除/更换", details=refs,
        )


def _video_path_ok(url: str) -> bool:
    """受管路径（/outputs/...）且文件存在可读（P0-03：安全解析）"""
    from .managed_paths import VIDEO_SUFFIXES, resolve_managed_asset

    p = resolve_managed_asset(url, allowed_types=VIDEO_SUFFIXES)
    try:
        return p is not None and p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


# =====================================================================
# 镜头序列（跨场景按项目顺序排列，用于续接/返工级联）
# =====================================================================
def project_shots(project_id: str) -> list:
    """项目全部镜头，按 场景顺序 → 镜内顺序 排成一条线性序列"""
    seq: list = []
    for sc in list_scenes(project_id):
        for shot in sc["shots"]:
            seq.append(shot)
    return seq


def rework_from(project_id: str, shot_id: str, cascade: bool = False,
                comment: str = "") -> dict:
    """镜头返工：把目标镜头打回草稿；cascade=True 时后续镜头全部打回。

    返回 {"reset": [shot_id...]}。已锁定的审核记录不受影响。
    """
    seq = project_shots(project_id)
    ids = [s["id"] for s in seq]
    if shot_id not in ids:
        raise ValueError("镜头不属于该项目")
    idx = ids.index(shot_id)
    targets = ids[idx:] if cascade else [shot_id]
    for sid in targets:
        shot = get_shot(sid)
        if not shot or shot["status"] in ("queued", "generating"):
            continue  # 正在生成的不动
        sb_from = shot["status"]
        set_shot_status(sid, "draft")
        log_review(
            sid, project_id, action="rework",
            from_status=sb_from, to_status="draft",
            comment=comment or ("从此镜头返工（含后续）" if cascade else "返工重做"),
        )
    return {"reset": targets}


# =====================================================================
# 审核历史（第二批：质量审核留痕）
# =====================================================================
def log_review(shot_id: str, project_id: str, action: str, from_status: str = "",
               to_status: str = "", version_id: str = "", comment: str = "",
               checks: list = None, stage: str = "") -> dict:
    rid = _new_id("rvh")
    return db.insert_and_get(
        "review_history",
        ["id", "shot_id", "project_id", "action", "from_status", "to_status",
         "version_id", "comment", "checks", "stage", "created_at"],
        (rid, shot_id, project_id, action, from_status, to_status, version_id,
         comment, json.dumps(checks or [], ensure_ascii=False), stage, _utcnow()),
        rid,
    )


def list_review_history(project_id: str, limit: int = 200) -> list:
    """项目审核历史（带镜头标题，按时间倒序）"""
    rows = _rows(
        "SELECT h.*, s.title AS shot_title FROM review_history h"
        " LEFT JOIN shots s ON h.shot_id = s.id"
        " WHERE h.project_id = ? ORDER BY h.created_at DESC LIMIT ?",
        (project_id, limit),
    )
    for r in rows:
        try:
            r["checks"] = json.loads(r.get("checks") or "[]")
        except (ValueError, TypeError):
            r["checks"] = []
    return rows


# =====================================================================
# Shot versions（候选版本）
# =====================================================================
def create_version(shot_id: str, job: dict, video_url: str = "",
                   poster_url: str = "") -> dict:
    vid = _new_id("ver")
    max_no = _row(
        "SELECT COALESCE(MAX(version_no), 0) AS m FROM shot_versions WHERE shot_id = ?",
        (shot_id,),
    )["m"]
    params = {
        k: job.get(k)
        for k in ("engine", "width", "height", "num_frames", "fps", "steps",
                  "guidance", "seed", "preset", "preview", "hardware",
                  "first_frame_image", "last_frame_image",
                  "source_video_version_id")
        if job.get(k) is not None
    }
    # PR-04：角色快照随版本存档（入队时冻结，锁定生成所用角色数据）
    chars_snap = job.get("characters_snapshot") or "[]"
    if not isinstance(chars_snap, str):
        chars_snap = json.dumps(chars_snap, ensure_ascii=False)
    return db.insert_and_get(
        "shot_versions",
        ["id", "shot_id", "job_id", "version_no", "prompt", "negative_prompt",
         "engine", "params", "video_url", "poster_url", "seed", "status",
         "characters_snapshot", "created_at"],
        (vid, shot_id, job["id"], max_no + 1, job.get("prompt", ""),
         job.get("negative_prompt", "") or "", job.get("engine", ""),
         json.dumps(params, ensure_ascii=False), video_url, poster_url,
         job.get("seed", -1), "candidate", chars_snap, _utcnow()),
        vid,
    )


def _decode_version(v: dict) -> None:
    """解码版本附加 JSON：角色快照 → characters 列表（PR-04）。"""
    try:
        v["characters"] = json.loads(v.get("characters_snapshot") or "[]")
    except (ValueError, TypeError):
        v["characters"] = []


def list_versions(shot_id: str) -> list:
    rows = _rows(
        "SELECT * FROM shot_versions WHERE shot_id = ? ORDER BY version_no DESC",
        (shot_id,),
    )
    for v in rows:
        _decode_version(v)
    return rows


def get_version(version_id: str) -> Optional[dict]:
    v = _row("SELECT * FROM shot_versions WHERE id = ?", (version_id,))
    if v:
        _decode_version(v)
    return v


def adopt_version(shot_id: str, version_id: str) -> None:
    """采用某版本：current_version_id 指向它，镜头回到待审核/已通过态由调用方定。

    后端强制校验（3A）：
    - 版本归属：跨镜头采用直接拒绝（不能把 A 镜头的视频采到 B 镜头）；
    - 文件有效：受管路径且文件存在、非空；
    - 引用保护：镜头被时间线引用时不得静默更换采用指针（409 + 片段明细）；
    - 审核有效性：已通过镜头换采用版本后回到视频审核（新版本不继承旧版本的
      approved），与指针切换在同一事务里原子完成。
    """
    v = get_version(version_id)
    if not v:
        raise ValueError(f"版本不存在: {version_id}")
    if v["shot_id"] != shot_id:
        raise ValueError("版本不属于该镜头，拒绝采用")
    if not _video_path_ok(v.get("video_url") or ""):
        raise ValueError(
            f"版本视频文件缺失或不可读: {v.get('video_url') or '(空)'}，拒绝采用"
        )
    shot = get_shot(shot_id)
    if not shot:
        raise ValueError(f"镜头不存在: {shot_id}")
    now = _utcnow()
    if shot["current_version_id"] != version_id:
        _guard_no_references(shot_references(shot_id), "该镜头（更换采用版本）")
    # 原子写：采用指针（+ 已通过镜头换版本时回到视频审核，新版本不继承 approved）
    with db.transaction() as conn:
        conn.execute(
            "UPDATE shots SET current_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, now, shot_id),
        )
        if shot.get("status") == "approved":
            conn.execute(
                "UPDATE shots SET status = 'review', review_stage = 'video',"
                " updated_at = ? WHERE id = ?",
                (now, shot_id),
            )


def delete_version(shot_id: str, version_id: str) -> None:
    """删除版本（带归属校验）；若删的是当前采用版本，回退到最新候选。

    3A：删当前版本会更换采用指针——镜头被时间线引用时拒绝（409 + 明细）。
    """
    v = get_version(version_id)
    if not v:
        raise ValueError(f"版本不存在: {version_id}")
    if v["shot_id"] != shot_id:
        raise ValueError("版本不属于该镜头，拒绝删除")
    shot = get_shot(shot_id)
    if shot and shot["current_version_id"] == version_id:
        _guard_no_references(shot_references(shot_id), "该镜头（删除当前采用版本）")
    _write("DELETE FROM shot_versions WHERE id = ?", (version_id,))
    shot = get_shot(shot_id)
    if shot and shot["current_version_id"] == version_id:
        rest = list_versions(shot_id)
        _write(
            "UPDATE shots SET current_version_id = ? WHERE id = ?",
            (rest[0]["id"] if rest else "", shot_id),
        )


# =====================================================================
# 审核（3A：三态分离 + 质检前置 + 原子写入）
# =====================================================================
def _review_quality(issues: list) -> dict:
    from .shotcheck import quality_score

    return {"issues": issues, "score": quality_score(issues)}


def review_shot(shot_id: str, action: str, version_id: str = "", comment: str = "",
                stage: str = "video", expected_version_id: str = "") -> dict:
    """审核入口（数据层原子版）。

    - stage="design"：设计审核（生成前）。只检查创作参数，不要求有生成版本。
      approve → review_stage="design_ok"；rework → review_stage="design"。
      生成状态机不变（draft 保持 draft）。
    - stage="video"（默认，兼容旧行为）：视频审核。先对**候选版本**做只读
      质检，阻断时状态与 current_version_id 均保持原值（修 S1）；
      通过后在同一事务原子写「采用指针 + 审核状态 + 审核记录」。
    - expected_version_id：提交时复核，与镜头当前采用版本不一致 → 409
      （客户端基于旧视图提交）。
    """
    from .shotcheck import check_design, check_shot

    if action not in ("approve", "rework"):
        raise ValueError(f"未知审核动作: {action}")
    if stage not in ("design", "video"):
        raise ValueError(f"未知审核阶段: {stage}")

    shot = get_shot(shot_id)
    if not shot:
        raise ValueError("分镜不存在")
    scene = get_scene(shot["scene_id"])
    project_id = scene["project_id"] if scene else ""
    from_status = shot["status"]

    # ---------- 设计审核 ----------
    if stage == "design":
        issues = check_design(shot)
        if action == "approve":
            blocking = [i for i in issues if i["level"] == "error"]
            if blocking:
                log_review(shot_id, project_id, action="design_blocked",
                           from_status=from_status, to_status=from_status,
                           comment=comment or "存在阻断性设计问题，设计审核未通过",
                           checks=issues, stage="design")
                raise ReviewBlockedError(
                    "存在阻断性设计问题，不能通过设计审核；请先解决后再试",
                    issues=issues, score=_review_quality(issues)["score"],
                )
            to_stage = "design_ok"
        else:
            to_stage = "design"
        _write(
            "UPDATE shots SET review_stage = ?, updated_at = ? WHERE id = ?",
            (to_stage, _utcnow(), shot_id),
        )
        log_review(shot_id, project_id, action=action,
                   from_status=from_status, to_status=from_status,
                   comment=comment, checks=issues, stage="design")
        result = get_shot(shot_id)
        result["quality"] = _review_quality(issues)
        return result

    # ---------- 视频审核 ----------
    if action == "approve":
        candidate_id = shot["current_version_id"]
        if version_id:
            version = get_version(version_id)
            if not version:
                raise ValueError("版本不存在")
            if version["shot_id"] != shot_id:
                raise ValueError("版本不属于该镜头，无法采用")
            if not _video_path_ok(version.get("video_url") or ""):
                raise ValueError(
                    f"版本视频文件缺失或不可读: {version.get('video_url') or '(空)'}，无法采用"
                )
            candidate_id = version_id
            if shot["current_version_id"] != version_id:
                # 更换采用指针：被时间线引用的镜头不允许静默更换（3A）
                _guard_no_references(shot_references(shot_id), "该镜头（更换采用版本）")
        elif not candidate_id:
            raise ValueError("还没有生成任何版本，无法审核通过")

        if expected_version_id and shot["current_version_id"] != expected_version_id:
            raise StaleSubmissionError(
                "提交时采用版本已变化（镜头已被其他操作修改），请刷新后重试"
            )

        # 质检前置：候选版本在内存中替换，不写库——阻断时指针与状态均不变（S1）
        fresh = dict(shot)
        fresh["current_version_id"] = candidate_id
        fresh["current_version"] = next(
            (v for v in shot.get("versions") or [] if v["id"] == candidate_id), None
        )
        issues = check_shot(fresh)
        blocking = [i for i in issues if i["level"] == "error"]
        if blocking:
            log_review(shot_id, project_id, action="approve_blocked",
                       from_status=from_status, to_status=from_status,
                       version_id=candidate_id,
                       comment=comment or "存在阻断性问题，审核未通过",
                       checks=issues, stage="video")
            raise ReviewBlockedError(
                "存在阻断性问题，不能通过审核；请先解决后再试",
                issues=issues, score=_review_quality(issues)["score"],
            )

        # 原子写：采用指针 + 审核状态 + 审核记录（同一事务）
        now = _utcnow()
        rid = _new_id("rvh")
        with db.transaction() as conn:
            if version_id and shot["current_version_id"] != version_id:
                conn.execute(
                    "UPDATE shots SET current_version_id = ?, updated_at = ? WHERE id = ?",
                    (version_id, now, shot_id),
                )
            conn.execute(
                "UPDATE shots SET status = 'approved', review_stage = '',"
                " updated_at = ? WHERE id = ?",
                (now, shot_id),
            )
            conn.execute(
                "INSERT INTO review_history (id, shot_id, project_id, action,"
                " from_status, to_status, version_id, comment, checks, stage,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, shot_id, project_id, "approve", from_status, "approved",
                 candidate_id, comment, json.dumps(issues, ensure_ascii=False),
                 "video", now),
            )
    else:
        # 打回重做：检查结果仅作留痕，不阻断
        issues = check_shot(shot)
        now = _utcnow()
        rid = _new_id("rvh")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE shots SET status = 'rework', review_stage = '',"
                " updated_at = ? WHERE id = ?",
                (now, shot_id),
            )
            conn.execute(
                "INSERT INTO review_history (id, shot_id, project_id, action,"
                " from_status, to_status, version_id, comment, checks, stage,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, shot_id, project_id, "rework", from_status, "rework",
                 version_id or shot.get("current_version_id") or "", comment,
                 json.dumps(issues, ensure_ascii=False), "video", now),
            )

    result = get_shot(shot_id)
    result["quality"] = _review_quality(issues)
    return result


# =====================================================================
# 剧本解析（规则式「AI 拆分」：场景标题 / 镜头段落 / 人物对白）
# =====================================================================
_SCENE_PATTERNS = [
    re.compile(r"^\s*(?:#{1,3}\s*)?场景[一二三四五六七八九十百\d]+[：:．.\s]?(.*)$"),
    re.compile(r"^\s*(?:#{1,3}\s*)?SCENE\s*\d+[：:.\s]*(.*)$", re.IGNORECASE),
    re.compile(r"^\s*(?:#{1,3}\s*)?第[一二三四五六七八九十百\d]+场[：:．.\s]?(.*)$"),
    re.compile(r"^\s*={3,}.*={3,}\s*$"),
]

_DIALOGUE_PAT = re.compile(r"^\s*([^\s：:]{1,12})\s*[：:]\s*(.+)$")
_CAMERA_PAT = re.compile(r"(推|拉|摇|移|跟|升|降|环绕|固定|手持|变焦)")
_SHOTSIZE_PAT = re.compile(r"(远景|全景|中景|近景|特写|大特写)")
_DURATION_PAT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|s\b|S\b)")


def _match_scene(line: str) -> Optional[str]:
    for pat in _SCENE_PATTERNS:
        m = pat.match(line)
        if m:
            title = (m.group(1) if m.lastindex else "").strip()
            return title or line.strip("=# 　")
    return None


def parse_script(text: str) -> list:
    """把纯文本剧本拆成 [{title, description, shots:[{...}]}]。

    支持格式（宽松识别）：
        场景一：密室        /  ### 场景2：雨夜街道
        第3场：客栈
        === 分隔线 ===
    场景内每条非空行视为一个镜头；「人物：台词」识别为对白；
    「(远景, 推, 3秒)」或行内 远景/推/3秒 自动提取景别、运动、时长。
    """
    scenes: list = []
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("//"):
            continue
        scene_title = _match_scene(line)
        if scene_title is not None:
            current = {"title": scene_title or "新场景", "description": "", "shots": []}
            scenes.append(current)
            continue
        if current is None:
            # 没有场景标记时自动建默认场景
            current = {"title": "场景一", "description": "", "shots": []}
            scenes.append(current)

        # 行首对白：整行都是「人物：台词」→ 合并进上一条镜头的对白（同一镜头有声台词）
        dialogue = ""
        body = line.strip()
        dm = _DIALOGUE_PAT.match(body)
        dialogue_only = bool(dm) and not body.startswith(("场景", "第", "SCENE"))
        if dialogue_only and current["shots"]:
            last = current["shots"][-1]
            last["dialogue"] = (last["dialogue"] + " " + body).strip() if last["dialogue"] else body
            continue
        if dm and not body.startswith(("场景", "第", "SCENE")):
            dialogue = body
            body = dm.group(2).strip()

        # 元信息提取：优先括号内容（远景, 推, 4秒），无括号再回退正文搜索
        paren = ""
        pm = re.search(r"[（(]([^）)]*)[）)]", body)
        if pm:
            paren = pm.group(1)
        meta_src = paren or body

        size = "中景"
        ms = _SHOTSIZE_PAT.search(meta_src)
        if ms:
            size = ms.group(1)
        movement = "固定"
        mm = _CAMERA_PAT.search(meta_src)
        if mm:
            movement = "固定" if mm.group(1) == "固定" else mm.group(1) + "镜"
        duration = 3.0
        md = _DURATION_PAT.search(meta_src)
        if md:
            duration = float(md.group(1))

        # 标题清洗：仅剥括号元信息与显式景别/时长词，保留正文原字
        title = re.sub(r"[（(][^）)]*[）)]", "", body)
        title = _SHOTSIZE_PAT.sub("", title)
        title = _DURATION_PAT.sub("", title)
        title = re.sub(r"[\s,，、]+$", "", title.strip(" ,，。"))

        current["shots"].append(
            {
                "title": title[:40] or "新镜头",
                "dialogue": dialogue,
                "shot_size": size,
                "camera_movement": movement,
                "duration_s": duration,
                "prompt": body,
            }
        )
    return scenes


def script_preview(project_id: str, text: str) -> dict:
    """解析剧本并与现有分镜做差异对比，**不写库**——供导入前确认。

    第三批（S3）：预览绑定内容哈希与项目 revision，提交时校验，
    预览之后项目被修改则拒绝过期提交。
    """
    import hashlib

    parsed = parse_script(text)
    existing_scenes = list_scenes(project_id)
    existing_titles = {sc["title"].strip() for sc in existing_scenes}
    new_titles = [ps["title"].strip() for ps in parsed]
    project = get_project(project_id) or {}
    return {
        "mode": "preview",
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "project_revision": project.get("revision") or 0,
        "existing": {
            "scene_count": len(existing_scenes),
            "shot_count": sum(len(sc["shots"]) for sc in existing_scenes),
        },
        "incoming": {
            "scene_count": len(parsed),
            "shot_count": sum(len(s["shots"]) for s in parsed),
            "scene_titles": new_titles,
        },
        # 标题撞车：重建模式下这些场景会被替换；追加模式下会跳过
        "overlap_titles": [t for t in new_titles if t in existing_titles],
    }


def _destructive_loss(project_id: str) -> dict:
    """rebuild 将损失的成果明细（场景/镜头/已生成版本/时间线）

    注意：list_scenes 的分镜不带 versions 字段，版本数必须直查
    shot_versions 表（曾经用 shot.get("versions") 恒为 0，漏统计）。
    """
    scenes = list_scenes(project_id)
    shot_count = sum(len(sc["shots"]) for sc in scenes)
    version_count = _row(
        "SELECT COUNT(*) AS c FROM shot_versions v"
        " JOIN shots s ON v.shot_id = s.id"
        " JOIN scenes sc ON s.scene_id = sc.id WHERE sc.project_id = ?",
        (project_id,),
    )["c"]
    timeline_count = _row(
        "SELECT COUNT(*) AS c FROM timeline_items WHERE project_id = ?", (project_id,)
    )["c"]
    return {
        "scene_count": len(scenes),
        "shot_count": shot_count,
        "version_count": version_count,
        "timeline_count": timeline_count,
    }


def import_script(project_id: str, text: str, mode: str = "rebuild",
                  expected_revision: Optional[int] = None,
                  content_hash: Optional[str] = None,
                  confirm: bool = False) -> dict:
    """解析剧本并写入场景/分镜。

    - rebuild（默认）：清空该项目场景与分镜后重建。整个重建在**单个事务**
      里执行，中途任何一步失败都整体回滚，不会再出现「删了旧的、新的没建成」
      的半破坏现场。
    - append：保留已有场景与分镜，把解析结果追加进去；标题与现有场景相同的
      场景跳过（不覆盖已有制作成果）。

    第三批保护（S3，服务端强制，不依赖前端先调预览）：
    - content_hash 传入时必须与 text 实际哈希一致，否则 400；
    - expected_revision 传入时必须与项目当前 revision 一致，否则 409
      （预览之后项目被改过，提交已过期）；
    - rebuild 且已有制作成果（版本/时间线）时必须 confirm=True，
      否则 409 并返回将损失的明细；确认后先做一致性备份再重建；
    - 每次成功导入 revision +1（与预览绑定）。
    """
    import hashlib

    if mode not in ("rebuild", "append"):
        raise ValueError(f"未知导入模式: {mode}")
    if content_hash is not None:
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual != content_hash:
            raise ValueError("剧本内容与预览时不一致（content_hash 不匹配），请重新预览")
    project = get_project(project_id)
    if not project:
        raise ValueError("项目不存在")
    if expected_revision is not None and expected_revision != (project.get("revision") or 0):
        raise StalePreviewError(
            f"项目已在预览后被修改（revision {project.get('revision') or 0} != {expected_revision}），"
            "请重新预览后再提交"
        )

    parsed = parse_script(text)
    if not parsed:
        raise ValueError("剧本中没有解析出任何场景（请检查「场景一：xxx」格式标记）")

    removed = {"scenes": 0, "shots": 0}
    backup_path = None
    if mode == "rebuild":
        existing = list_scenes(project_id)
        removed["scenes"] = len(existing)
        removed["shots"] = sum(len(sc["shots"]) for sc in existing)
        loss = _destructive_loss(project_id)
        if (loss["version_count"] or loss["timeline_count"]) and not confirm:
            raise DestructiveImportError(
                "覆盖重建将删除已生成的镜头版本/时间线片段，请确认后重试"
                "（confirm=true）；或改用 append 模式保留已有成果",
                details=loss,
            )
        if loss["version_count"] or loss["timeline_count"]:
            # 确认后的破坏性重建：先一致性备份（可恢复），再进事务
            backup_path = str(db.auto_backup("aivideo-pre-rebuild"))

    now = _utcnow()
    created_scenes = []
    # 单事务：场景/分镜重建 + 剧本文本更新 + revision 自增一起提交或整体回滚
    #（旧代码把 update_project(script=text) 放在事务外，重建失败也会
    # 留下半升级的剧本文本——审查项 S3）
    with db.transaction() as conn:
        if mode == "rebuild":
            conn.execute(
                "DELETE FROM timeline_items WHERE project_id = ?", (project_id,)
            )
            conn.execute("DELETE FROM scenes WHERE project_id = ?", (project_id,))
        elif mode == "append":
            # 追加模式：标题相同的场景跳过，保留其镜头的制作成果
            existing_titles = {
                r["title"].strip()
                for r in conn.execute(
                    "SELECT title FROM scenes WHERE project_id = ?", (project_id,)
                ).fetchall()
            }
            parsed = [ps for ps in parsed if ps["title"].strip() not in existing_titles]

        scene_base = conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) AS m"
            " FROM scenes WHERE project_id = ?", (project_id,),
        ).fetchone()["m"]
        for i, ps in enumerate(parsed):
            sid = _new_id("scn")
            conn.execute(
                "INSERT INTO scenes (id, project_id, title, description,"
                " environment, lighting, order_index, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, project_id, ps["title"].strip() or "新场景", ps["description"],
                 "", "", scene_base + 1 + i, now, now),
            )
            for j, ps_shot in enumerate(ps["shots"]):
                conn.execute(
                    "INSERT INTO shots (id, scene_id, title, dialogue, shot_size,"
                    " camera_movement, duration_s, prompt, negative_prompt,"
                    " reference_image, character_ids, mode, continue_from,"
                    " order_index, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_new_id("sht"), sid, ps_shot["title"], ps_shot["dialogue"],
                     ps_shot["shot_size"], ps_shot["camera_movement"],
                     ps_shot["duration_s"], ps_shot["prompt"], "", "",
                     "[]", "t2v", "", j, now, now),
                )
            created_scenes.append(sid)
        conn.execute(
            "UPDATE projects SET script = ?, revision = COALESCE(revision, 0) + 1,"
            " updated_at = ? WHERE id = ?",
            (text, now, project_id),
        )

    scenes = [get_scene(sid) for sid in created_scenes]
    n_shots = sum(
        _row("SELECT COUNT(*) AS c FROM shots WHERE scene_id = ?", (sid,))["c"]
        for sid in created_scenes
    )
    return {
        "mode": mode,
        "scenes": scenes,
        "scene_count": len(scenes),
        "shot_count": n_shots,
        "removed": removed,
        "backup": backup_path,
        "revision": (get_project(project_id) or {}).get("revision") or 0,
    }


# =====================================================================
# Timeline
# =====================================================================
def list_timeline(project_id: str) -> list:
    """时间线片段（带镜头摘要，不含未生成视频的镜头可占位）"""
    items = _rows(
        "SELECT t.*, s.title AS shot_title, s.status AS shot_status,"
        " s.dialogue AS dialogue, s.duration_s, s.current_version_id"
        " FROM timeline_items t JOIN shots s ON t.shot_id = s.id"
        " WHERE t.project_id = ? ORDER BY t.position",
        (project_id,),
    )
    for it in items:
        v = get_version(it["current_version_id"]) if it["current_version_id"] else None
        it["video_url"] = v["video_url"] if v else ""
        it["poster_url"] = v["poster_url"] if v else ""
    return items


def timeline_layout(project_id: str) -> list:
    """时间线布局：每个片段带计算好的 start_s / end_s / seg_dur（裁剪后）

    供字幕自动生成、配音对齐、导出混音共用同一套时间基准。
    """
    from .managed_paths import VIDEO_SUFFIXES, resolve_managed_asset
    from .video import probe_duration

    items = list_timeline(project_id)
    t = 0.0
    for it in items:
        dur = 0.0
        if it.get("video_url"):
            p = resolve_managed_asset(it["video_url"], allowed_types=VIDEO_SUFFIXES)
            if p and p.exists():
                dur = probe_duration(p)
        t_in = min(float(it.get("trim_in_s") or 0), max(dur - 0.3, 0))
        t_out = float(it.get("trim_out_s") or 0)
        end = min(t_out, dur) if t_out > 0 else dur
        seg_dur = max(end - t_in, 0.3)
        it["start_s"] = round(t, 3)
        it["end_s"] = round(t + seg_dur, 3)
        it["seg_dur"] = round(seg_dur, 3)
        it["trim_in_eff"] = round(t_in, 3)
        t += seg_dur
    return items


def sync_timeline(project_id: str) -> list:
    """把项目里所有有视频的镜头同步进时间线（保留已有裁剪设置）"""
    existing = {
        it["shot_id"]: it
        for it in _rows(
            "SELECT * FROM timeline_items WHERE project_id = ?", (project_id,)
        )
    }
    pos = 0
    for sc in list_scenes(project_id):
        for shot in sc["shots"]:
            if shot["status"] not in ("review", "approved") or not shot["current_version_id"]:
                continue
            if shot["id"] in existing:
                pos += 1
                continue
            _write(
                "INSERT INTO timeline_items (id, project_id, shot_id, position,"
                " trim_in_s, trim_out_s, transition, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (_new_id("tl"), project_id, shot["id"], pos, 0.0, 0.0, "none", _utcnow()),
            )
            pos += 1
    return list_timeline(project_id)


def update_timeline_item(item_id: str, **fields) -> Optional[dict]:
    allowed = {"position", "trim_in_s", "trim_out_s", "transition"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return _row("SELECT * FROM timeline_items WHERE id = ?", (item_id,))
    sql = f"UPDATE timeline_items SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), item_id))
    return _row("SELECT * FROM timeline_items WHERE id = ?", (item_id,))


def reorder_timeline(project_id: str, item_ids: list) -> list:
    for i, iid in enumerate(item_ids):
        _write("UPDATE timeline_items SET position = ? WHERE id = ?", (i, iid))
    return list_timeline(project_id)


def remove_timeline_item(item_id: str) -> None:
    _write("DELETE FROM timeline_items WHERE id = ?", (item_id,))


# =====================================================================
# Exports
# =====================================================================
def create_export(project_id: str) -> dict:
    eid = _new_id("exp")
    return db.insert_and_get(
        "exports",
        ["id", "project_id", "status", "created_at"],
        (eid, project_id, "pending", _utcnow()),
        eid,
    )


def update_export(export_id: str, **fields) -> None:
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    sql = f"UPDATE exports SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), export_id))


def get_export(export_id: str) -> Optional[dict]:
    return _row("SELECT * FROM exports WHERE id = ?", (export_id,))


def list_exports(project_id: str) -> list:
    return _rows(
        "SELECT * FROM exports WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    )


# =====================================================================
# helpers
# =====================================================================
def _touch_project(project_id: str) -> None:
    _write("UPDATE projects SET updated_at = ? WHERE id = ?", (_utcnow(), project_id))


def _touch_scene_project(scene_id: str) -> None:
    sc = get_scene(scene_id)
    if sc:
        _touch_project(sc["project_id"])
