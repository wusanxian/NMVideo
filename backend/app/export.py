"""导出 Worker：后台线程把时间线拼接为 MP4（第二批：多轨混音 + 字幕烧录）

流程：
  1. 视频轨：concat_videos 拼接已通过镜头（统一分辨率/非等比加黑边）
  2. 音频混音：对白配音（按时间线落位）+ 音乐轨 + 音效轨 → amix
  3. 字幕：SRT 烧录（libass；失败自动退软字幕 mov_text）
  4. 最终封装 MP4（faststart）
"""
import shutil
import json
import subprocess
import threading
from pathlib import Path
from typing import Optional

from . import storyboard as sb
from . import tracks
from .config import OUTPUTS_DIR
from .managed_paths import MEDIA_SUFFIXES, resolve_managed_asset
from .video import concat_videos, get_ffmpeg, has_audio_stream, probe_duration

EXPORTS_DIR = OUTPUTS_DIR / "exports"


def _url_to_path(url: str) -> Optional[Path]:
    """/outputs/xxx.mp3 -> 本机路径；越界/非白名单/外链返回 None（P0-03）。

    base_dir 显式传本模块的 OUTPUTS_DIR（保持历史上 export 模块级绑定
    的语义；app.config 未被 reassign 时两者一致）。
    """
    return resolve_managed_asset(url, allowed_types=MEDIA_SUFFIXES,
                                 base_dir=OUTPUTS_DIR)


def _exists(url: str) -> bool:
    p = _url_to_path(url)
    return p is not None and p.is_file()


def _tempo_chain(speed: float) -> str:
    """atempo 滤镜链：单片只支持 0.5~2.0，超出部分级联（0.25~4.0 最多两级）。"""
    parts = []
    remaining = float(speed)
    while remaining > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def _track_overrun_warnings(layout: list, audio_tracks: list) -> list:
    """音频越界警告（PR-11 验收：音频越界时给出警告，不静默截断）。

    只提示不阻断：音乐/音效超出片尾的部分导出时本来就会被 -t 截断，
    但用户应当在导出前/后都看得见。
    """
    total = max((float(it.get("end_s") or 0) for it in layout), default=0)
    if total <= 0:
        return []
    from .video import probe_duration
    warns = []
    for t in audio_tracks or []:
        path = _url_to_path(t.get("audio_url") or "")
        if not path or not path.is_file():
            continue
        start = float(t.get("start_s") or 0)
        t_in = float(t.get("trim_in_s") or 0)
        t_out = float(t.get("trim_out_s") or 0)
        dur = probe_duration(path)
        length = (t_out - t_in) if t_out > t_in else max(dur - t_in, 0)
        end = start + length
        if end > total + 0.05:
            kind = "音效" if (t.get("kind") == "sfx") else "音乐"
            title = t.get("title") or "未命名"
            warns.append(
                f"{kind}「{title}」超出片尾 {end - total:.1f} 秒，导出时将被截断")
    return warns


def preview_warnings(project_id: str) -> list:
    """时间线页实时越界预览（不创建导出记录也能看）。"""
    layout = sb.timeline_layout(project_id)
    return _track_overrun_warnings(layout, tracks.list_audio_tracks(project_id))


def _mix_audio(project_id: str, layout: list, out_wav: Path, manifest=None) -> bool:
    """视频原声 + 对白 + 音乐 + 音效；统一按冻结时间线裁剪、落位和补静音。"""
    ffmpeg = get_ffmpeg()
    duration = max((float(it.get("end_s") or 0) for it in layout), default=0)
    if duration <= 0:
        return False

    # (path, timeline_start, source_trim_in, duration, volume, tempo)
    sources: list = []

    def add_source(path, start, trim_in=0.0, trim_out=0.0, volume=1.0, length=None,
                   tempo=1.0):
        start, trim_in = float(start), max(float(trim_in), 0.0)
        # 负起点意味着只取进入时间线范围后的那一段声音。
        seek = trim_in + max(-start, 0.0)
        start = max(start, 0.0)
        available = duration - start
        if trim_out > 0:
            available = min(available, trim_out - seek)
        if length is not None:
            available = min(available, length)
        if available > 0 and volume != 0:
            sources.append((path, start, seek, available, volume, float(tempo or 1.0)))

    from .dialogue_timing import schedule
    voices = (manifest["voices"] if manifest is not None else
              list({v["id"]: v for it in layout for v in tracks.list_voices_of_shot(it["shot_id"])}.values()))
    _, cues = schedule(layout, voices)
    for it in layout:
        video_path = _url_to_path(it.get("video_url") or "")
        if video_path and has_audio_stream(video_path):
            # PR-11 后半：原声跟随片段变速（source_duration_s 是源时间轴裁剪时长，
            # atempo 压缩/拉伸后正好填满时间线槽位 seg_dur = source/speed）
            add_source(video_path, it["start_s"], it.get("trim_in_eff") or 0.0,
                       length=it.get("source_duration_s", it["end_s"] - it["start_s"]),
                       tempo=float(it.get("speed") or 1.0))
    for cue in cues:
        audio_path = _url_to_path(cue.get("audio_url") or "")
        if not audio_path or not audio_path.is_file():
            raise ValueError("配音素材缺失，请重新生成后导出")
        add_source(audio_path, cue["start_s"], length=cue["duration_s"])
    for t in (manifest["audio_tracks"] if manifest is not None else tracks.list_audio_tracks(project_id)):
        track_path = _url_to_path(t.get("audio_url") or "")
        if track_path:
            start = float(t.get("start_s") or 0)
            t_in = float(t.get("trim_in_s") or 0)
            t_out = float(t.get("trim_out_s") or 0)
            add_source(track_path, start, t_in, t_out, float(t.get("volume", 0.8)))
    if not sources:
        return False

    cmd = [ffmpeg, "-y"]
    for s in sources:
        cmd += ["-i", str(s[0])]

    filters = []
    labels = []
    for i, (path, start, t_in, length, volume, tempo) in enumerate(sources):
        chain = f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
        chain += f",atrim=start={t_in:.6f}:duration={length:.6f},asetpts=PTS-STARTPTS"
        # 变速片段的原声压缩/拉伸（atempo 链支持 0.25~4.0）
        if abs(tempo - 1.0) > 1e-6:
            chain += "," + _tempo_chain(tempo)
        # 裁剪后 adelay 的前导静音可能没有 PTS，按样本重新计时再交给 amix。
        chain += (f",volume={volume:.2f},adelay={int(start * 1000)}|{int(start * 1000)}"
                  ",asetpts=N/SR/TB")
        chain += f"[a{i}]"
        filters.append(chain)
        labels.append(f"[a{i}]")
    mix = ("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0"
           f",apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[mix]")
    filters.append(mix)

    cmd += ["-filter_complex", ";".join(filters), "-map", "[mix]", "-t", f"{duration:.6f}",
            "-c:a", "pcm_s16le", str(out_wav)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"混音失败: {proc.stderr[-500:]}")
    return out_wav.exists()


def _subtitle_path(project_id: str, tmp_dir: Path, manifest=None) -> Optional[Path]:
    items = manifest["subtitles"] if manifest is not None else tracks.list_subtitles(project_id)
    if not any((i.get("text") or "").strip() for i in items):
        return None
    srt = tmp_dir / "subtitles.srt"
    srt.write_text(tracks.format_srt(items), encoding="utf-8")
    return srt


def run_export(export_id: str) -> None:
    """线程体：读取时间线 → 拼接 → 混音 → 字幕 → 更新 exports 记录"""
    exp = sb.get_export(export_id)
    if not exp:
        return
    project_id = exp["project_id"]
    tmp_dir = EXPORTS_DIR / f".tmp_{export_id}"
    try:
        sb.update_export(export_id, status="running", stage="准备片段", progress=0.02)
        manifest = json.loads(exp["manifest"]) if exp.get("manifest") else None
        if manifest is not None and manifest.get("schema_version") != 1:
            raise ValueError("导出清单版本不受支持")
        layout = sb.layout_from_items(manifest["items"]) if manifest is not None else sb.timeline_layout(project_id)
        if not layout:
            raise ValueError("时间线为空：请先在时间线页添加镜头")

        output = (manifest or {}).get("output", {})
        if manifest is not None:
            from .dialogue_timing import schedule
            layout, _ = schedule(layout, manifest["voices"], extend=output.get("extend_dialogue", True))

        seg_inputs = []
        for it in layout:
            if not it.get("video_url"):
                raise ValueError(f"镜头「{it['shot_title']}」还没有生成视频")
            path = _url_to_path(it["video_url"])
            if not path or not path.exists():
                raise FileNotFoundError(f"视频文件缺失或不受管: {it['video_url']}")
            seg_inputs.append(
                {
                    "path": str(path),
                    "trim_in_s": it.get("trim_in_eff") or 0.0,
                    "trim_out_s": float(it.get("trim_out_s") or 0),
                    "transition": it.get("transition") or "none",
                    "duration_s": it.get("seg_dur"),
                    "speed": float(it.get("speed") or 1.0),
                    "fit": output.get("fit", "contain"),
                }
            )

        from .video import probe_resolution

        w, h = probe_resolution(Path(seg_inputs[0]["path"]))
        if not w or not h:
            w, h = 512, 512
        w, h = {"portrait": (1080, 1920), "landscape": (1920, 1080),
                "square": (1080, 1080)}.get(output.get("canvas"), (w, h))

        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # PR-06：帧率参数化——导出记录冻结所选帧率；旧记录（fps=0）
        # 回落项目帧率，再回落 24（与主流视频帧率一致）
        fps = int(exp.get("fps") or 0)
        if fps <= 0:
            proj = sb.get_project(project_id) or {}
            fps = int(proj.get("fps") or 0) or 24
        raw_video = tmp_dir / "concat.mp4"
        concat_videos(
            seg_inputs,
            raw_video,
            width=w,
            height=h,
            fps=fps,
            on_progress=lambda p, s: sb.update_export(
                export_id, progress=round(0.02 + p * 0.5, 4), stage=s
            ),
        )

        # ---------- 混音 ----------
        mixed_wav = tmp_dir / "mix.wav"
        has_audio = False
        try:
            has_audio = (_mix_audio(project_id, layout, mixed_wav, manifest) if manifest is not None
                         else _mix_audio(project_id, layout, mixed_wav))
        except Exception as e:
            # 请求包含音轨时，不能把混音失败当作无声导出成功。
            raise RuntimeError(f"混音失败，请检查音频素材后重试：{e}") from e
        sb.update_export(export_id, progress=0.58,
                         stage="混音完成" if has_audio else "无音频轨")

        # ---------- 字幕 ----------
        srt = (_subtitle_path(project_id, tmp_dir, manifest) if manifest is not None
               else _subtitle_path(project_id, tmp_dir))
        out_path = EXPORTS_DIR / f"{export_id}.mp4"
        _final_mux(raw_video, mixed_wav if has_audio else None, srt, out_path,
                   on_progress=lambda p, s: sb.update_export(
                       export_id, progress=round(0.6 + p * 0.38, 4), stage=s))

        sb.update_export(
            export_id,
            status="done",
            progress=1.0,
            stage="导出完成",
            file_url=f"/outputs/exports/{export_id}.mp4",
            # PR-11 验收：音频越界时给出警告（随导出记录持久化，UI 可回看）
            warnings=json.dumps(_track_overrun_warnings(
                layout,
                manifest["audio_tracks"] if manifest is not None
                else tracks.list_audio_tracks(project_id),
            ), ensure_ascii=False),
            finished_at=_now(),
        )
    except Exception as e:
        sb.update_export(
            export_id, status="failed", error=str(e), stage="失败", finished_at=_now()
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _final_mux(video: Path, audio: Optional[Path], srt: Optional[Path],
               out_path: Path, on_progress=None) -> None:
    """视频 + 混音 + 字幕 最终封装。字幕优先烧录，失败退软字幕。"""
    ffmpeg = get_ffmpeg()
    duration = probe_duration(video)

    def _run(burn: bool) -> subprocess.CompletedProcess:
        cmd = [ffmpeg, "-y", "-i", str(video)]
        audio_idx = srt_idx = None
        n_inputs = 1
        if audio:
            cmd += ["-i", str(audio)]
            audio_idx = n_inputs
            n_inputs += 1
        if srt and not burn:
            cmd += ["-i", str(srt)]
            srt_idx = n_inputs
            n_inputs += 1

        if srt and burn:
            # 烧录：subtitles 滤镜（libass）。Windows 路径需转义冒号
            srt_arg = str(srt).replace("\\", "/").replace(":", "\\:")
            vf = (f"subtitles='{srt_arg}':force_style='FontName=Microsoft YaHei,"
                  "FontSize=18,PrimaryColour=&HFFFFFF,OutlineColour=&H80000000,"
                  "BorderStyle=1,Outline=1,MarginV=36,MarginL=24,MarginR=24'")
            cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        else:
            cmd += ["-c:v", "copy"]

        cmd += ["-map", "0:v"]
        if audio_idx is not None:
            cmd += ["-map", f"{audio_idx}:a", "-c:a", "aac", "-b:a", "160k"]
        else:
            cmd += ["-an"]
        if srt_idx is not None:
            cmd += ["-map", f"{srt_idx}:0", "-c:s", "mov_text"]
        if duration > 0:
            # 以实际拼接视频为最终边界，长音乐/字幕不能拉长成片；短音乐也不能截短。
            cmd += ["-t", f"{duration:.6f}"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    if srt:
        proc = _run(burn=True)
        if proc.returncode != 0:
            # 烧录失败（缺字体/滤镜问题）→ 软字幕兜底
            print(f"[export] 字幕烧录失败，退软字幕: {proc.stderr[-200:]}")
            proc = _run(burn=False)
            if proc.returncode != 0:
                raise RuntimeError(f"最终封装失败: {proc.stderr[-400:]}")
    else:
        proc = _run(burn=False)
        if proc.returncode != 0:
            raise RuntimeError(f"最终封装失败: {proc.stderr[-400:]}")
    if on_progress:
        on_progress(1.0, "封装完成")


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_export(export_id: str) -> None:
    t = threading.Thread(target=run_export, args=(export_id,), daemon=True,
                         name=f"export-{export_id}")
    t.start()
