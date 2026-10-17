"""多轨道数据层（第二批）：对白配音轨 / 音乐轨 / 音效轨 / 字幕轨

- shot_voices：镜头对白配音（TTS 产物，属于「对白轨」）
- audio_tracks：音乐 / 音效素材片段（可多条，带起止与音量）
- subtitle_tracks：字幕片段（可由镜头对白自动生成，可编辑）

第二阶段 2A：读/写经 db 统一接口，不再直接触摸 db._conn/db._lock。
"""
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
# 对白配音轨（shot_voices）
# =====================================================================
def create_voice(shot_id: str, **fields) -> dict:
    vid = _new_id("voc")
    return db.insert_and_get(
        "shot_voices",
        ["id", "shot_id", "character_id", "character_name", "voice", "text",
         "audio_url", "duration_s", "engine", "status", "created_at"],
        (
            vid, shot_id,
            fields.get("character_id", ""), fields.get("character_name", ""),
            fields.get("voice", ""), fields.get("text", ""),
            fields.get("audio_url", ""), float(fields.get("duration_s") or 0),
            fields.get("engine", ""), fields.get("status", "done"), _utcnow(),
        ),
        vid,
    )


def list_voices_of_shot(shot_id: str) -> list:
    return _rows(
        "SELECT * FROM shot_voices WHERE shot_id = ? ORDER BY created_at", (shot_id,)
    )


def get_voice(voice_id: str) -> Optional[dict]:
    return _row("SELECT * FROM shot_voices WHERE id = ?", (voice_id,))


def delete_voice(voice_id: str) -> None:
    v = get_voice(voice_id)
    _write("DELETE FROM shot_voices WHERE id = ?", (voice_id,))
    if v and v.get("audio_url"):
        from .managed_paths import AUDIO_SUFFIXES, resolve_managed_asset

        p = resolve_managed_asset(v["audio_url"], allowed_types=AUDIO_SUFFIXES)
        if p:
            p.unlink(missing_ok=True)


# =====================================================================
# 音乐 / 音效轨（audio_tracks）
# =====================================================================
def create_audio_track(project_id: str, **fields) -> dict:
    aid = _new_id("aud")
    max_pos = _row(
        "SELECT COALESCE(MAX(position), -1) AS m FROM audio_tracks WHERE project_id = ?",
        (project_id,),
    )["m"]
    return db.insert_and_get(
        "audio_tracks",
        ["id", "project_id", "kind", "title", "audio_url", "start_s",
         "trim_in_s", "trim_out_s", "volume", "position", "created_at"],
        (
            aid, project_id, fields.get("kind", "music"), fields.get("title", ""),
            fields.get("audio_url", ""), float(fields.get("start_s") or 0),
            float(fields.get("trim_in_s") or 0), float(fields.get("trim_out_s") or 0),
            float(fields.get("volume", 0.8)), fields.get("position", max_pos + 1),
            _utcnow(),
        ),
        aid,
    )


def list_audio_tracks(project_id: str, kind: Optional[str] = None) -> list:
    if kind:
        return _rows(
            "SELECT * FROM audio_tracks WHERE project_id = ? AND kind = ?"
            " ORDER BY position, created_at",
            (project_id, kind),
        )
    return _rows(
        "SELECT * FROM audio_tracks WHERE project_id = ? ORDER BY position, created_at",
        (project_id,),
    )


def update_audio_track(track_id: str, **fields) -> Optional[dict]:
    allowed = {"title", "start_s", "trim_in_s", "trim_out_s", "volume", "position"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return _row("SELECT * FROM audio_tracks WHERE id = ?", (track_id,))
    sql = f"UPDATE audio_tracks SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), track_id))
    return _row("SELECT * FROM audio_tracks WHERE id = ?", (track_id,))


def delete_audio_track(track_id: str) -> None:
    t = _row("SELECT * FROM audio_tracks WHERE id = ?", (track_id,))
    _write("DELETE FROM audio_tracks WHERE id = ?", (track_id,))
    if t and t.get("audio_url"):
        from .managed_paths import AUDIO_SUFFIXES, resolve_managed_asset

        p = resolve_managed_asset(t["audio_url"], allowed_types=AUDIO_SUFFIXES)
        if p:
            p.unlink(missing_ok=True)


# =====================================================================
# 字幕轨（subtitle_tracks）
# =====================================================================
def create_subtitle(project_id: str, **fields) -> dict:
    sid = _new_id("sub")
    max_pos = _row(
        "SELECT COALESCE(MAX(position), -1) AS m FROM subtitle_tracks WHERE project_id = ?",
        (project_id,),
    )["m"]
    return db.insert_and_get(
        "subtitle_tracks",
        ["id", "project_id", "shot_id", "text", "start_s", "end_s", "position",
         "created_at"],
        (
            sid, project_id, fields.get("shot_id", ""), fields.get("text", ""),
            float(fields.get("start_s") or 0), float(fields.get("end_s") or 0),
            fields.get("position", max_pos + 1), _utcnow(),
        ),
        sid,
    )


def list_subtitles(project_id: str) -> list:
    return _rows(
        "SELECT * FROM subtitle_tracks WHERE project_id = ? ORDER BY position, start_s",
        (project_id,),
    )


def update_subtitle(sub_id: str, **fields) -> Optional[dict]:
    allowed = {"text", "start_s", "end_s", "position"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return _row("SELECT * FROM subtitle_tracks WHERE id = ?", (sub_id,))
    sql = f"UPDATE subtitle_tracks SET {','.join(f'{k} = ?' for k in fields)} WHERE id = ?"
    _write(sql, (*fields.values(), sub_id))
    return _row("SELECT * FROM subtitle_tracks WHERE id = ?", (sub_id,))


def delete_subtitle(sub_id: str) -> None:
    _write("DELETE FROM subtitle_tracks WHERE id = ?", (sub_id,))


def replace_subtitles(project_id: str, items: list) -> list:
    """整轨替换（自动生成 / 批量保存用）"""
    _write("DELETE FROM subtitle_tracks WHERE project_id = ?", (project_id,))
    out = []
    for i, it in enumerate(items):
        out.append(
            create_subtitle(
                project_id,
                shot_id=it.get("shot_id", ""),
                text=it.get("text", ""),
                start_s=float(it.get("start_s") or 0),
                end_s=float(it.get("end_s") or 0),
                position=i,
            )
        )
    return out


def format_srt(items: list) -> str:
    """字幕片段列表 → SRT 文本"""
    def _ts(sec: float) -> str:
        ms = int(round(max(sec, 0) * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, it in enumerate(items, 1):
        text = (it.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{_ts(it.get('start_s') or 0)} --> {_ts(it.get('end_s') or 0)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)
