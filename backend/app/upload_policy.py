"""上传策略：允许的后缀与体积上限（纯数据，不依赖 Web 框架）。

为什么单独成模块：`app/routers/assets.py` 在导入期就要注册 FastAPI 路由，
而带文件参数的路由要求安装 `python-multipart`；本机 venv 缺这个包时，
**连导入都会抛错**。把「收什么文件、放多大」放进无依赖的模块后，
这条规则本身可以在任何环境下被测试，而不是只能靠人肉确认。

视频上限单独放宽：白模生成/替换要传原始素材，15 秒 1080p 就有 30~80MB，
用图片音频的 50MB 上限会把正常素材挡在门外。
"""
from .managed_paths import VIDEO_SUFFIXES

# 图片 / 音频（沿用既有白名单，不扩大——放开的是视频这一类）
IMAGE_AUDIO_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".ogg", ".m4a", ".flac"}
)
# 视频：与 managed_paths 的受管视频后缀保持一致，
# 否则「能传上来但后续 resolve_managed_asset 不认」会变成半截功能。
ALLOWED_UPLOAD_VIDEO = frozenset(VIDEO_SUFFIXES)

MAX_UPLOAD_MB = 50
MAX_UPLOAD_VIDEO_MB = 300

KIND_VIDEO = "video"
KIND_MEDIA = "media"


def classify(suffix: str) -> str:
    """按后缀判定上传类别：返回 ``video`` / ``media`` / 空串（不支持）。"""
    suffix = (suffix or "").lower()
    if suffix in ALLOWED_UPLOAD_VIDEO:
        return KIND_VIDEO
    if suffix in IMAGE_AUDIO_SUFFIXES:
        return KIND_MEDIA
    return ""


def limit_mb(kind: str) -> int:
    return MAX_UPLOAD_VIDEO_MB if kind == KIND_VIDEO else MAX_UPLOAD_MB
