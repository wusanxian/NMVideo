"""导出 Worker：后台线程把时间线拼接为 MP4（第二批：多轨混音 + 字幕烧录）

流程：
  1. 视频轨：concat_videos 拼接已通过镜头（统一分辨率/非等比加黑边）
  2. 音频混音：对白配音（按时间线落位）+ 音乐轨 + 音效轨 → amix
  3. 字幕：SRT 烧录（libass；失败自动退软字幕 mov_text）
  4. 最终封装 MP4（faststart）
"""
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from . import storyboard as sb
from . import tracks
from .config import OUTPUTS_DIR
from .managed_paths import MEDIA_SUFFIXES, resolve_managed_asset
from .video import concat_videos, get_ffmpeg, probe_duration

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


def _mix_audio(project_id: str, layout: list, out_wav: Path) -> bool:
    """对白 + 音乐 + 音效混音为单条 WAV。没有音频源返回 False。"""
    ffmpeg = get_ffmpeg()

    # 收集音频源：[(path, start_s, end_s(裁剪), volume)]
    sources: list = []
    for it in layout:
        for v in tracks.list_voices_of_shot(it["shot_id"]):
            audio_path = _url_to_path(v.get("audio_url") or "")
            if audio_path:
                # 配音落在片段起点；超出片段长度的部分由 atrim 裁掉
                sources.append((
                    audio_path,
                    it["start_s"], it["end_s"],
                    1.0,
                ))
    for t in tracks.list_audio_tracks(project_id):
        track_path = _url_to_path(t.get("audio_url") or "")
        if track_path:
            start = float(t.get("start_s") or 0)
            t_in = float(t.get("trim_in_s") or 0)
            t_out = float(t.get("trim_out_s") or 0)
            sources.append((
                track_path,
                start, (start + t_out) if t_out > 0 else None,
                float(t.get("volume", 0.8) or 0.8),
                t_in,
            ))
    if not sources:
        return False

    cmd = [ffmpeg, "-y"]
    for s in sources:
        cmd += ["-i", str(s[0])]

    filters = []
    labels = []
    for i, s in enumerate(sources):
        path, start, end, volume = s[0], s[1], s[2], s[3]
        t_in = s[4] if len(s) > 4 else 0.0
        chain = f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
        if t_in > 0:
            chain += f",atrim=start={t_in:.3f},asetpts=PTS-STARTPTS"
        if end is not None:
            chain += f",atrim=end={max(end - start, 0.1):.3f},asetpts=PTS-STARTPTS"
        chain += f",volume={volume:.2f},adelay={int(start * 1000)}|{int(start * 1000)}"
        chain += f"[a{i}]"
        filters.append(chain)
        labels.append(f"[a{i}]")
    mix = "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0[mix]"
    filters.append(mix)

    cmd += ["-filter_complex", ";".join(filters), "-map", "[mix]",
            "-c:a", "pcm_s16le", str(out_wav)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"混音失败: {proc.stderr[-500:]}")
    return out_wav.exists()


def _subtitle_path(project_id: str, tmp_dir: Path) -> Optional[Path]:
    items = tracks.list_subtitles(project_id)
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
        layout = sb.timeline_layout(project_id)
        if not layout:
            raise ValueError("时间线为空：请先在时间线页添加镜头")

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
                }
            )

        from .video import probe_resolution

        w, h = probe_resolution(Path(seg_inputs[0]["path"]))
        if not w or not h:
            w, h = 512, 512

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
            has_audio = _mix_audio(project_id, layout, mixed_wav)
        except Exception as e:
            # 混音失败不阻断导出（视频轨本身有效）
            print(f"[export] 混音失败，导出无音频版本: {e}")
        sb.update_export(export_id, progress=0.58,
                         stage="混音完成" if has_audio else "无音频轨")

        # ---------- 字幕 ----------
        srt = _subtitle_path(project_id, tmp_dir)
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
                  "BorderStyle=1,Outline=1'")
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
        cmd += ["-movflags", "+faststart", str(out_path)]
        return subprocess.run(cmd, capture_output=True, text=True)

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
