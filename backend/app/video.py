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
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 合成失败: {proc.stderr[-800:]}")
    return out_path


def read_video_metadata(video_path: Path) -> dict:
    """读回 MP4 里嵌的生成参数；没有则返回空 dict。"""
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-f", "ffmetadata", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"读取视频参数失败: {proc.stderr.decode('utf-8', errors='replace')[-500:]}")
    text = proc.stdout.decode("utf-8")
    match = re.search(r"(?m)^comment=((?:\\[\s\S]|[^\\\n])*)", text)
    if match:
        value = re.sub(r"\\([\s\S])", r"\1", match.group(1))
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def remux_video(src_path: Path, out_path: Path, metadata: dict = None) -> Path:
    """原生视频原样归档为 MP4（2C：免抽帧重编码）。

    ComfyUI 的 VHS_VideoCombine 等节点直接产出视频——旧流程把它拆成 PNG
    帧再 libx264 重编码，既慢又有画质损失。这里：
      - mp4/mov 容器：`-c copy` 直接拷贝原始流 + 写 comment 元数据
        （码流原样、秒级完成，FreeVideo 式「视频自带参数」不丢）
      - 其它容器（webm/gif）：video→video 转码（不经过 PNG 帧序列，
        仍远优于抽帧重编码）；remux 失败也自动降级到此分支
    """
    ffmpeg = get_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta_args = []
    if metadata:
        meta_args = ["-metadata", f"comment={json.dumps(metadata, ensure_ascii=False)}"]
    if src_path.suffix.lower() in (".mp4", ".mov", ".m4v"):
        cmd = [ffmpeg, "-y", "-i", str(src_path), "-c", "copy",
               "-movflags", "+faststart", *meta_args, str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return out_path
        out_path.unlink(missing_ok=True)  # remux 失败：转码兜底
    cmd = [ffmpeg, "-y", "-i", str(src_path),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           *meta_args, str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"视频归档失败: {proc.stderr[-500:]}")
    return out_path


def extract_first_frame(video_path: Path, out_path: Path) -> Path:
    """抽取视频首帧作海报（2C：原生视频直通后画廊/审核仍要有封面）。"""
    ffmpeg = get_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-i", str(video_path),
           "-frames:v", "1", "-update", "1", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"海报帧抽取失败: {proc.stderr[-300:]}")
    return out_path


def probe_duration(video_path: Path) -> float:
    """读取视频时长（秒）；失败返回 0。注意：Duration 在 ffmpeg 的 stderr 输出里"""
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-i", str(video_path), "-f", "ffmetadata", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
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
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", proc.stderr)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def has_audio_stream(media_path: Path) -> bool:
    """检测视频是否包含原声音轨，供导出混音使用（静音片段不创建无效输入）。"""
    proc = subprocess.run(
        [get_ffmpeg(), "-i", str(media_path), "-f", "ffmetadata", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return bool(re.search(r"Stream .*Audio:", proc.stderr))


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
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
            # PR-11 后半：片段变速——时间线时长 = 源时长 / speed。
            # speed=1 时 output_seg == seg_dur，与原行为完全一致。
            speed = float(it.get("speed") or 1.0)
            if speed <= 0:
                speed = 1.0
            output_seg = seg_dur / speed
            output_dur = max(output_seg, float(it.get("duration_s") or output_seg))
            if output_dur > output_seg + 0.001:
                # 先物理裁切，保证延长时复制剪出点末帧，不会读到被剪掉的画面。
                # FFmpeg 7 的 trim EOF 与 tpad 组合会丢末帧，独立文件保留完整时间基准。
                trimmed = tmp_dir / f"trim_{i:03d}.mp4"
                cut = subprocess.run(
                    [ffmpeg, "-y", "-ss", f"{t_in:.6f}", "-i", str(src),
                     "-t", f"{seg_dur:.6f}", "-an", "-c:v", "libx264",
                     "-preset", "veryfast", "-crf", "18", str(trimmed)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                if cut.returncode != 0:
                    raise RuntimeError(f"延长片段前裁切失败: {cut.stderr[-400:]}")
                src, t_in = trimmed, 0.0
                seg_dur = probe_duration(trimmed)
                output_seg = seg_dur / speed

            vf = [
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                # 变速：setpts 输出时长 = 原时长 / 系数 → 提速 s 倍用 (1/s)*PTS
                # （在补尾帧之前；speed=2 压半、speed=0.5 拉长一倍）
                *( [f"setpts={1.0 / speed:.6f}*PTS"] if abs(speed - 1.0) > 1e-6 else [] ),
                # 给 fps 滤镜补一个尾帧，再按输出时长截断，防止缩放后每段丢末帧而累积声画错位。
                f"tpad=stop_mode=clone:stop_duration={max(output_dur - output_seg, 0) + 1 / fps:.6f}",
                f"fps={fps}",
                "setsar=1",
                "format=yuv420p",
            ]
            if it.get("fit") == "cover":
                vf[0:2] = [f"scale={width}:{height}:force_original_aspect_ratio=increase",
                           f"crop={width}:{height}"]
            if it.get("transition") == "fade":
                vf.append(f"fade=t=in:st=0:d=0.3")
                vf.append(f"fade=t=out:st={max(output_dur - 0.3, 0):.3f}:d=0.3")
            seg = tmp_dir / f"seg_{i:03d}.mp4"
            cmd = [
                ffmpeg, "-y",
                "-ss", f"{t_in:.3f}",
                "-i", str(src),
                "-t", f"{output_dur:.3f}",
                "-vf", ",".join(vf),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-an", str(seg),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(f"拼接失败: {proc.stderr[-500:]}")
        if on_progress:
            on_progress(1.0, "导出完成")
        return out_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
