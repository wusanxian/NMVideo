"""服务配置持久化：settings.json（LLM / 图像生成 / ComfyUI 等外部服务参数）

环境变量优先，settings.json 兜底；前端设置页写 JSON。
"""
import json
import os
import threading
from pathlib import Path

from .config import BASE_DIR

SETTINGS_FILE = Path(os.environ.get("AIVIDEO_SETTINGS", BASE_DIR / "settings.json"))

DEFAULTS = {
    "llm": {
        "base_url": os.environ.get("AIVIDEO_LLM_BASE_URL", ""),
        "api_key": os.environ.get("AIVIDEO_LLM_API_KEY", ""),
        "model": os.environ.get("AIVIDEO_LLM_MODEL", ""),
    },
    "imagegen": {
        "base_url": os.environ.get("AIVIDEO_IMAGEGEN_BASE_URL", ""),
        "api_key": os.environ.get("AIVIDEO_IMAGEGEN_API_KEY", ""),
        "model": os.environ.get("AIVIDEO_IMAGEGEN_MODEL", ""),
    },
    "comfy_url": os.environ.get("AIVIDEO_COMFYUI_URL", "http://127.0.0.1:8188"),
}

_lock = threading.Lock()


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> dict:
    """读全量配置（环境变量默认值 + 文件覆盖）"""
    with _lock:
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                return _deep_merge(DEFAULTS, data)
            except (ValueError, OSError):
                pass
        return dict(DEFAULTS)


def update_settings(patch: dict) -> dict:
    """合并更新并落盘"""
    with _lock:
        current = {}
        if SETTINGS_FILE.exists():
            try:
                current = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                current = {}
        merged = _deep_merge(current, patch or {})
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return _deep_merge(DEFAULTS, merged)


def get_section(name: str) -> dict:
    return get_settings().get(name, {})
