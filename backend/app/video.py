"""帧序列 → MP4 合成（使用 imageio-ffmpeg 自带的静态 ffmpeg，无需系统安装）

支持把生成参数以 JSON 形式写入 MP4 的 comment 元数据（FreeVideo 式
「视频自带工作流」）：画廊可读回参数一键复用/升级渲染。
"""
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg


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
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
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
