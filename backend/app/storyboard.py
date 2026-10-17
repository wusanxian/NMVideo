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
    return shot


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
        "character_ids", "mode", "continue_from",
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
               checks: list = None) -> dict:
    rid = _new_id("rvh")
    return db.insert_and_get(
        "review_history",
        ["id", "shot_id", "project_id", "action", "from_status", "to_status",
         "version_id", "comment", "checks", "created_at"],
        (rid, shot_id, project_id, action, from_status, to_status, version_id,
         comment, json.dumps(checks or [], ensure_ascii=False), _utcnow()),
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
                  "guidance", "seed", "preset", "preview", "hardware")
        if job.get(k) is not None
    }
    return db.insert_and_get(
        "shot_versions",
        ["id", "shot_id", "job_id", "version_no", "prompt", "negative_prompt",
         "engine", "params", "video_url", "poster_url", "seed", "status",
         "created_at"],
        (vid, shot_id, job["id"], max_no + 1, job.get("prompt", ""),
         job.get("negative_prompt", "") or "", job.get("engine", ""),
         json.dumps(params, ensure_ascii=False), video_url, poster_url,
         job.get("seed", -1), "candidate", _utcnow()),
        vid,
    )


def list_versions(shot_id: str) -> list:
    return _rows(
        "SELECT * FROM shot_versions WHERE shot_id = ? ORDER BY version_no DESC",
        (shot_id,),
    )


def get_version(version_id: str) -> Optional[dict]:
    return _row("SELECT * FROM shot_versions WHERE id = ?", (version_id,))


def adopt_version(shot_id: str, version_id: str) -> None:
    """采用某版本：current_version_id 指向它，镜头回到待审核/已通过态由调用方定。

    后端强制校验版本归属：跨镜头采用直接拒绝（不能把 A 镜头的视频采到 B 镜头）。
    """
    v = get_version(version_id)
    if not v:
        raise ValueError(f"版本不存在: {version_id}")
    if v["shot_id"] != shot_id:
        raise ValueError("版本不属于该镜头，拒绝采用")
    _write(
        "UPDATE shots SET current_version_id = ? WHERE id = ?", (version_id, shot_id)
    )


def delete_version(shot_id: str, version_id: str) -> None:
    """删除版本（带归属校验）；若删的是当前采用版本，回退到最新候选。"""
    v = get_version(version_id)
    if not v:
        raise ValueError(f"版本不存在: {version_id}")
    if v["shot_id"] != shot_id:
        raise ValueError("版本不属于该镜头，拒绝删除")
    _write("DELETE FROM shot_versions WHERE id = ?", (version_id,))
    shot = get_shot(shot_id)
    if shot and shot["current_version_id"] == version_id:
        rest = list_versions(shot_id)
        _write(
            "UPDATE shots SET current_version_id = ? WHERE id = ?",
            (rest[0]["id"] if rest else "", shot_id),
        )


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
    """解析剧本并与现有分镜做差异对比，**不写库**——供导入前确认。"""
    parsed = parse_script(text)
    existing_scenes = list_scenes(project_id)
    existing_titles = {sc["title"].strip() for sc in existing_scenes}
    new_titles = [ps["title"].strip() for ps in parsed]
    return {
        "mode": "preview",
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


def import_script(project_id: str, text: str, mode: str = "rebuild") -> dict:
    """解析剧本并写入场景/分镜。

    - rebuild（默认）：清空该项目场景与分镜后重建。整个重建在**单个事务**
      里执行，中途任何一步失败都整体回滚，不会再出现「删了旧的、新的没建成」
      的半破坏现场。
    - append：保留已有场景与分镜，把解析结果追加进去；标题与现有场景相同的
      场景跳过（不覆盖已有制作成果）。
    """
    if mode not in ("rebuild", "append"):
        raise ValueError(f"未知导入模式: {mode}")
    parsed = parse_script(text)
    if not parsed:
        raise ValueError("剧本中没有解析出任何场景（请检查「场景一：xxx」格式标记）")

    removed = {"scenes": 0, "shots": 0}
    if mode == "rebuild":
        existing = list_scenes(project_id)
        removed["scenes"] = len(existing)
        removed["shots"] = sum(len(sc["shots"]) for sc in existing)

    now = _utcnow()
    created_scenes = []
    # 单事务：场景/分镜重建 + 剧本文本更新一起提交或整体回滚
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
            "UPDATE projects SET script = ?, updated_at = ? WHERE id = ?",
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
    from .config import OUTPUTS_DIR
    from .video import probe_duration

    items = list_timeline(project_id)
    t = 0.0
    for it in items:
        dur = 0.0
        if it.get("video_url"):
            p = OUTPUTS_DIR / it["video_url"][len("/outputs/"):]
            if p.exists():
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
