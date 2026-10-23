"""音频波形数据（改造.md PR-11 后半：多轨可视化）

把任意受管音频解成 PCM 后按桶取峰值，供前端时间线音轨画波形条。
磁盘缓存：同一文件（路径+大小+mtime）+ 相同点数只解一次。
"""
import hashlib
import json
import struct
import subprocess
from pathlib import Path

from .config import OUTPUTS_DIR
from .managed_paths import AUDIO_SUFFIXES, resolve_managed_asset
from .video import get_ffmpeg

WAVEFORMS_DIR = OUTPUTS_DIR / "waveforms"

MIN_POINTS, MAX_POINTS = 16, 600
# 解码采样率：8kHz 单声道足够画波形，一个 5 分钟音频也只有 ~2.4MB PCM
_DECODE_RATE = 8000


def _cache_key(url: str, path: Path, points: int) -> str:
    stat = path.stat()
    raw = f"{url}|{points}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _decode_pcm(path: Path) -> bytes:
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar",
         str(_DECODE_RATE), "-f", "s16le", "pipe:1"],
        capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"音频解码失败: {proc.stderr[-300:].decode('utf-8', errors='replace')}")
    return proc.stdout


def peaks_for(url: str, points: int = 200) -> dict:
    """受管音频 URL → {peaks: [0~1...], duration_s, points}。

    越界/非白名单 URL 直接拒绝（与导出素材同一套受管路径校验）。
    """
    path = resolve_managed_asset(url, allowed_types=AUDIO_SUFFIXES,
                                 base_dir=OUTPUTS_DIR)
    if not path or not path.is_file():
        raise ValueError(f"音频不存在或不受管: {url}")
    points = max(MIN_POINTS, min(MAX_POINTS, int(points or 200)))

    WAVEFORMS_DIR.mkdir(parents=True, exist_ok=True)
    cache = WAVEFORMS_DIR / f"{_cache_key(url, path, points)}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cache.unlink(missing_ok=True)  # 脏缓存按未命中重算

    pcm = _decode_pcm(path)
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[:len(pcm) // 2 * 2])
    n = len(samples)
    if n == 0:
        raise ValueError(f"音频无可解码数据: {url}")
    bucket = n / points
    peaks = []
    for i in range(points):
        chunk = samples[int(i * bucket):max(int((i + 1) * bucket), int(i * bucket) + 1)]
        peaks.append(round(max(abs(s) for s in chunk) / 32768.0, 3))
    out = {"url": url, "points": points, "peaks": peaks,
           "duration_s": round(n / _DECODE_RATE, 3)}
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache)
    return out
