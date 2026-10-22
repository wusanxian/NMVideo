"""TTS 配音服务（第二批）

双引擎策略：
- edge-tts（微软神经网络 TTS，需联网）：中文音色自然，支持 rate/volume/pitch 调节
- pyttsx3（Windows SAPI5，离线）：edge-tts 不可用时的兜底，音色为本机系统语音

配音文件落 outputs/tts/{id}.mp3，经 /outputs 静态路由直接可播。
"""
import asyncio
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .config import OUTPUTS_DIR
from .video import get_ffmpeg, probe_duration

TTS_DIR = OUTPUTS_DIR / "tts"

# 离线兜底音色（SAPI5 一般至少有一个中文语音；没有时界面会提示）
_FALLBACK_VOICE = {
    "id": "sapi5-default",
    "name": "系统语音（离线）",
    "gender": "?",
    "locale": "zh-CN",
    "engine": "pyttsx3",
}

_voices_cache = {"ts": 0.0, "voices": []}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600.0  # 音色目录缓存 1 小时


def _clean_text(text: str) -> str:
    """清洗对白文本：去动作提示括号、限长，避免 TTS 读出旁白式噪音"""
    t = re.sub(r"[（(][^）)]{0,20}[）)]", "", text or "")  # （低声）这类舞台提示
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 300:
        raise ValueError("单条配音最多 300 字，请按对白分句生成，避免截断")
    return t


async def _edge_list_voices() -> list:
    import edge_tts

    voices = await edge_tts.list_voices()
    out = []
    for v in voices:
        loc = v.get("Locale", "")
        if not loc.startswith("zh"):
            continue
        if v.get("Status") == "Deprecated":
            continue
        name = v["ShortName"]
        # 展示名取 ShortName + 地域，避免依赖服务端 DisplayName 字段
        region = {
            "zh-CN": "大陆", "zh-TW": "台湾", "zh-HK": "香港",
        }.get(loc, loc)
        out.append(
            {
                "id": name,
                "name": f"{name}（{region}·{'女' if v.get('Gender') == 'Female' else '男'}）",
                "gender": "女" if v.get("Gender") == "Female" else "男",
                "locale": loc,
                "engine": "edge-tts",
            }
        )
    return out


def _sapi5_list_voices() -> list:
    """离线音色：读取 Windows SAPI5 已装语音"""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        voices = engine.getProperty("voices") or []
        out = []
        for i, v in enumerate(voices):
            name = getattr(v, "name", "") or f"voice-{i}"
            out.append(
                {
                    "id": v.id,
                    "name": f"{name}（离线）",
                    "gender": "?",
                    "locale": "zh-CN",
                    "engine": "pyttsx3",
                }
            )
        try:
            engine.stop()
        except Exception:
            pass
        return out
    except Exception:
        return []


def list_voices() -> list:
    """音色目录：优先 edge-tts 在线目录，失败退回本机 SAPI5"""
    with _cache_lock:
        if time.time() - _voices_cache["ts"] < _CACHE_TTL and _voices_cache["voices"]:
            return _voices_cache["voices"]
    voices: list = []
    try:
        voices = asyncio.run(_edge_list_voices())
    except Exception:
        voices = []
    if not voices:
        voices = _sapi5_list_voices()
    if not voices:
        voices = [dict(_FALLBACK_VOICE)]
    with _cache_lock:
        _voices_cache["ts"] = time.time()
        _voices_cache["voices"] = voices
    return voices


def tts_available() -> tuple:
    """返回 (是否有可用引擎, 说明)"""
    try:
        import edge_tts  # noqa: F401

        return True, "edge-tts 神经网络语音（联网）"
    except ImportError:
        pass
    try:
        import pyttsx3  # noqa: F401

        return True, "pyttsx3 系统语音（离线）"
    except ImportError:
        return False, "未安装 TTS 库（pip install edge-tts pyttsx3）"


async def _edge_synth(text: str, voice: str, out_path: Path, rate: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_path))


def _sapi5_synth(text: str, voice: str, out_path: Path, rate: str) -> None:
    """pyttsx3 只能存 wav；用 ffmpeg 转成 mp3 统一格式"""
    import pyttsx3

    wav_path = out_path.with_suffix(".wav")
    engine = pyttsx3.init()
    try:
        if voice and voice != "sapi5-default":
            engine.setProperty("voice", voice)
        # rate 形如 "+10%"；SAPI5 的 rate 是相对偏移量（-10~10）
        m = re.match(r"([+-]?\d+)%", rate or "")
        if m:
            base = engine.getProperty("rate") or 200
            engine.setProperty("rate", int(base * (1 + int(m.group(1)) / 100)))
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()
    finally:
        engine.stop()
    if not wav_path.exists():
        raise RuntimeError("SAPI5 合成失败（系统可能没有中文语音包）")
    import subprocess

    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "4",
         str(out_path)],
        capture_output=True, text=True,
    )
    wav_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"音频转码失败: {proc.stderr[-300:]}")


def synthesize(text: str, voice_id: str, out_path: Path, rate: str = "+0%") -> dict:
    """合成单段配音。返回 {"duration_s": float, "engine": str}"""
    text = _clean_text(text)
    if not text:
        raise ValueError("对白文本为空")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    engine_used = ""
    try:
        asyncio.run(_edge_synth(text, voice_id or "zh-CN-XiaoxiaoNeural", out_path, rate))
        engine_used = "edge-tts"
    except Exception:
        # 离线兜底：SAPI5
        _sapi5_synth(text, voice_id if voice_id.startswith("HKEY") else "sapi5-default",
                     out_path, rate)
        engine_used = "pyttsx3"

    if not out_path.exists():
        raise RuntimeError("配音文件未生成")
    return {"duration_s": probe_duration(out_path), "engine": engine_used}
