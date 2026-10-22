"""共享对白排程：同镜头逐条播放，超时可延长画面或明确阻止导出。"""
import math


def schedule(layout, voices, *, extend=False):
    from .managed_paths import AUDIO_SUFFIXES, resolve_managed_asset
    from .video import probe_duration

    arranged, cues = [], []
    cursor = 0.0
    for original in layout:
        item = dict(original)
        original_start = float(item.get("start_s") or 0)
        length = float(item.get("seg_dur") or
                       (float(item.get("end_s") or 0) - original_start))
        source_duration = item.get("source_duration_s", length)
        offset = 0.0
        selected = sorted((v for v in voices if v["shot_id"] == item["shot_id"]),
                          key=lambda v: v.get("created_at", ""))
        for voice in selected:
            duration = float(voice.get("duration_s") or 0)
            if duration <= 0:
                path = resolve_managed_asset(voice.get("audio_url", ""), allowed_types=AUDIO_SUFFIXES)
                duration = probe_duration(path) if path and path.is_file() else 0
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("无法读取配音时长，请重新生成或删除无效配音")
            cues.append({**voice, "start_s": round(cursor + offset, 6),
                         "end_s": round(cursor + offset + duration, 6),
                         "duration_s": duration})
            offset += duration
        if offset > length + 0.02 and not extend:
            raise ValueError(f"镜头「{item.get('shot_title', '')}」配音需要 {offset:.2f} 秒，"
                             f"画面仅 {length:.2f} 秒；请选择延长画面或缩短配音")
        length = max(length, offset) if extend else length
        item.update(start_s=round(cursor, 6), end_s=round(cursor + length, 6),
                    seg_dur=length, source_start_s=original_start, source_duration_s=source_duration)
        arranged.append(item)
        cursor += length
    return arranged, cues


def subtitles_for_cues(cues):
    """按每条实际配音范围分句；句内时长按字符分配，不冒充逐字识别。"""
    import re
    output = []
    for cue in cues:
        parts = [s.strip() for s in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", cue.get("text") or "") if s.strip()]
        weight = sum(len(s) for s in parts)
        start = cue["start_s"]
        for part in parts:
            end = start + cue["duration_s"] * len(part) / weight
            output.append({"shot_id": cue["shot_id"], "text": part,
                           "start_s": round(start, 3), "end_s": round(end, 3)})
            start = end
    return output
