"""冻结导出所用版本、剪辑、音轨、字幕与媒体内容。"""
import hashlib
from pathlib import Path

from .managed_paths import MEDIA_SUFFIXES, resolve_managed_asset


def capture(database, project_id: str, fps: int) -> dict:
    from .config import OUTPUTS_DIR
    with database.transaction() as connection:
        def rows(sql, args=(project_id,)):
            return [dict(row) for row in connection.execute(sql, args).fetchall()]
        items = rows(
            "SELECT t.*,s.title AS shot_title,s.duration_s,s.current_version_id,"
            "v.video_url FROM timeline_items t JOIN shots s ON t.shot_id=s.id "
            "LEFT JOIN shot_versions v ON v.id=s.current_version_id "
            "WHERE t.project_id=? ORDER BY t.position")
        voices = rows("SELECT DISTINCT v.* FROM shot_voices v JOIN timeline_items t "
                      "ON v.shot_id=t.shot_id WHERE t.project_id=? ORDER BY v.created_at,v.rowid")
        audio = rows("SELECT * FROM audio_tracks WHERE project_id=? ORDER BY position")
        subtitles = rows("SELECT * FROM subtitle_tracks WHERE project_id=? ORDER BY position")
    manifest = {"schema_version": 1, "fps": fps, "items": items,
                "voices": voices, "audio_tracks": audio, "subtitles": subtitles}
    for collection, key in ((items, "video_url"), (voices, "audio_url"), (audio, "audio_url")):
        for item in collection:
            url = item.get(key)
            if not url:
                continue
            source = resolve_managed_asset(url, allowed_types=MEDIA_SUFFIXES, base_dir=OUTPUTS_DIR)
            if not source or not source.is_file():
                raise ValueError(f"导出素材缺失或不受管：{url}")
            # 流式复制，避免把整段视频放进 API 进程内存。
            import tempfile
            destination = Path(OUTPUTS_DIR) / "assets"
            destination.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with tempfile.NamedTemporaryFile(dir=destination, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    with source.open("rb") as original:
                        for block in iter(lambda: original.read(1024 * 1024), b""):
                            digest.update(block)
                            stream.write(block)
                except BaseException:
                    stream.close()
                    temporary.unlink(missing_ok=True)
                    raise
            frozen = destination / f"{digest.hexdigest()}{source.suffix.lower()}"
            temporary.replace(frozen)
            item["source_url"] = url
            item["sha256"] = digest.hexdigest()
            item[key] = f"/outputs/assets/{frozen.name}"
    return manifest
