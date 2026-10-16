"""图像生成服务：OpenAI 兼容 /images/generations 客户端

用于角色参考图、场景概念图、镜头首帧（图生视频前置）。
产物保存到 outputs/assets/，返回 /outputs/assets/... URL。
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request
import uuid

from . import settings as settings_module
from .config import OUTPUTS_DIR

ASSETS_DIR = OUTPUTS_DIR / "assets"


class ImageGenError(Exception):
    """图像生成失败（未配置 / 网络 / 服务端错误）"""


def _cfg() -> dict:
    return settings_module.get_section("imagegen")


def is_configured() -> bool:
    c = _cfg()
    return bool(c.get("base_url") and c.get("model"))


def status() -> dict:
    c = _cfg()
    return {
        "configured": is_configured(),
        "base_url": c.get("base_url", ""),
        "model": c.get("model", ""),
        "has_api_key": bool(c.get("api_key")),
    }


def _save_image(data: bytes, ext: str) -> str:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"gen_{uuid.uuid4().hex[:10]}.{ext}"
    (ASSETS_DIR / name).write_bytes(data)
    return f"/outputs/assets/{name}"


def generate_image(prompt: str, *, size: str = "1024x1024", n: int = 1) -> list:
    """生成图片，返回 URL 列表。失败抛 ImageGenError"""
    c = _cfg()
    if not is_configured():
        raise ImageGenError(
            "图像生成服务未配置：请到「模型服务」页填写 base_url 和模型名"
            "（支持任意 OpenAI 兼容 images 端点）"
        )
    if not prompt or not prompt.strip():
        raise ImageGenError("提示词不能为空")

    base = c["base_url"].rstrip("/")
    url = f"{base}/images/generations"
    payload = {"model": c["model"], "prompt": prompt.strip(),
               "size": size, "n": max(1, min(int(n), 4))}
    headers = {"Content-Type": "application/json"}
    if c.get("api_key"):
        headers["Authorization"] = f"Bearer {c['api_key']}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        raise ImageGenError(f"图像服务返回 HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise ImageGenError(f"连不上图像服务（{base}）: {e.reason}")
    except (TimeoutError, OSError) as e:
        raise ImageGenError(f"图像请求超时或网络错误: {e}")

    items = data.get("data") or []
    if not items:
        raise ImageGenError("图像服务未返回图片（data 为空）")

    urls = []
    for item in items:
        b64 = item.get("b64_json")
        img_url = item.get("url")
        if b64:
            urls.append(_save_image(base64.b64decode(b64), "png"))
        elif img_url:
            # 远端图片：下载后本地化（前端统一走 /outputs 静态路径）
            try:
                with urllib.request.urlopen(img_url, timeout=60) as r:
                    ext = "png"
                    ct = r.headers.get("Content-Type", "")
                    if "jpeg" in ct or "jpg" in ct:
                        ext = "jpg"
                    elif "webp" in ct:
                        ext = "webp"
                    urls.append(_save_image(r.read(), ext))
            except Exception as e:
                raise ImageGenError(f"下载生成图片失败: {e}")
        else:
            raise ImageGenError("图像服务返回格式不支持（既无 b64_json 也无 url）")
    return urls


def test_connection() -> dict:
    try:
        urls = generate_image("a simple test image: red circle on white background",
                              size="512x512", n=1)
        return {"ok": True, "url": urls[0]}
    except ImageGenError as e:
        return {"ok": False, "error": str(e)}
