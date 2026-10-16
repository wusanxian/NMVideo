"""帧序列 → MP4 合成（使用 imageio-ffmpeg 自带的静态 ffmpeg，无需系统安装）

支持把生成参数以 JSON 形式写入 MP4 的 comment 元数据（FreeVideo 式
「视频自带工作流」）：画廊可读回参数一键复用/升级渲染。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg


def get_ffmpeg() -> str:
    """imageio-ffmpeg 自带的静态 ffmpeg 路径"""
    return imageio_ffmpeg.get_ffmpeg_exe()


def frames_to_video(frames_dir: Path, out_path: Path, fps: int, metadata: dict = None) -> Path:
    if not list(frames_dir.glob("frame_*.png")):
        raise FileNotFoundError(f"帧目录为空: {frames_dir}")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",  # x264 要求偶数边长
        "-movflags", "+faststart",
    ]
    if metadata:
        # comment 标签写 JSON（ffmpeg -metadata 传，特殊字符安全）
        cmd += ["-metadata", f"comment={json.dumps(metadata, ensure_ascii=False)}"]
    cmd += [str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 合成失败: {proc.stderr[-800:]}")
    return out_path


def read_video_metadata(video_path: Path) -> dict:
    """读回 MP4 里嵌的生成参数；没有则返回空 dict。"""
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-f", "ffmetadata", "-"],
        capture_output=True, text=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("comment="):
            try:
                return json.loads(line[len("comment="):])
            except (ValueError, TypeError):
                return {}
    return {}


def probe_duration(video_path: Path) -> float:
    """读取视频时长（秒）；失败返回 0。注意：Duration 在 ffmpeg 的 stderr 输出里"""
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-f", "ffmetadata", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def probe_resolution(video_path: Path) -> tuple:
    """读取视频分辨率 (w, h)；失败返回 (0, 0)"""
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-f", "ffmetadata", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", proc.stderr)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def extract_last_frame(video_path: Path, out_path: Path) -> Path:
    """抽取视频末帧（视频续接的起始帧）。

    ffmpeg 的输入 seek 离片尾太近（<0.2s 左右）可能落在最后一帧之后导致空输出，
    因此按多个候选点依次尝试，取第一个成功的。
    """
    ffmpeg = get_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dur = probe_duration(video_path)

    candidates: list = []
    if dur > 0.6:
        candidates.append(["-sseof", "-0.5"])          # 相对片尾，首选
    if dur > 0:
        candidates.append(["-ss", f"{max(dur - 0.4, 0):.3f}"])  # 绝对定位
    if dur > 0:
        candidates.append(["-ss", f"{dur / 2:.3f}"])   # 中点兜底（极短视频）

    last_err = ""
    for seek in candidates:
        cmd = [ffmpeg, "-y", *seek, "-i", str(video_path),
               "-frames:v", "1", "-update", "1", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return out_path
        last_err = proc.stderr[-200:]
        out_path.unlink(missing_ok=True)
    raise RuntimeError(f"末帧抽取失败: {last_err}")


def concat_videos(
    items: list,
    out_path: Path,
    width: int = 0,
    height: int = 0,
    fps: int = 16,
    on_progress=None,
) -> Path:
    """把多段视频裁剪/归一化后拼接成一个 MP4。

    items: [{"path": str, "trim_in_s": float, "trim_out_s": float, "transition": "none"|"fade"}]
    - trim_out_s <= 0 表示到片尾
    - transition=fade 时片段尾部淡出、下一段头部淡入（基础转场）
    """
    if not items:
        raise ValueError("拼接列表为空")
    ffmpeg = get_ffmpeg()
    tmp_dir = out_path.parent / f".concat_{out_path.stem}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    segments: list = []
    try:
        for i, it in enumerate(items):
            src = Path(it["path"])
            if not src.exists():
                raise FileNotFoundError(f"片段不存在: {src}")
            dur = probe_duration(src)
            t_in = max(float(it.get("trim_in_s") or 0), 0.0)
            t_out = float(it.get("trim_out_s") or 0)
            if t_out <= 0 or t_out > dur:
                t_out = dur
            if t_out <= t_in:
                t_out = min(dur, t_in + 0.5)
            seg_dur = t_out - t_in

            vf = [
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                f"fps={fps}",
                "format=yuv420p",
            ]
            if it.get("transition") == "fade":
                vf.append(f"fade=t=in:st=0:d=0.3")
                vf.append(f"fade=t=out:st={seg_dur - 0.3:.3f}:d=0.3")
            seg = tmp_dir / f"seg_{i:03d}.mp4"
            cmd = [
                ffmpeg, "-y",
                "-ss", f"{t_in:.3f}", "-t", f"{seg_dur:.3f}",
                "-i", str(src),
                "-vf", ",".join(vf),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-an", str(seg),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"片段 {i} 处理失败: {proc.stderr[-400:]}")
            segments.append(seg)
            if on_progress:
                on_progress(0.1 + 0.7 * (i + 1) / len(items), f"处理片段 {i + 1}/{len(items)}")

        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{s.as_posix()}'" for s in segments), encoding="utf-8"
        )
        proc = subprocess.run(
            [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(out_path),
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"拼接失败: {proc.stderr[-500:]}")
        if on_progress:
            on_progress(1.0, "导出完成")
        return out_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
