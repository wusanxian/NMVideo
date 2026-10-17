"""受管媒体路径解析：/outputs/... URL → 本机安全路径（Hotfix P0-03）

背景：审查发现多处 `OUTPUTS_DIR / url[len("/outputs/"):]` 直接拼接，
无法阻止目录越界——`/outputs/../outside_file.txt`、`/outputs/C:/x`
（Windows 下盘符片段会替换掉 base）、反斜杠（Windows 路径分隔符）、
`%2e%2e%2f` 编码穿越、符号链接逃逸。

所有把 URL 拼本机路径的位置都必须经过 resolve_managed_asset：
  1. 只接受受管前缀 /outputs/（相对 URL 或内嵌该段的同机绝对 URL）；
  2. 拒绝可疑编码（%）、反斜杠、控制字符、. / .. 路径段、盘符/绝对路径；
  3. realpath 规范化后必须仍在 base_dir（默认 OUTPUTS_DIR）内——
     符号链接指向目录外同样拒绝；
  4. 扩展名白名单（调用方按图片/视频/音频传入 allowed_types）。
解析失败返回 None：调用方决定 400、跳过该素材还是标缺失。

默认根目录在**调用时**动态读 `app.config.OUTPUTS_DIR`（旧代码多为函数内
`from .config import OUTPUTS_DIR`，测试靠 monkeypatch config 隔离目录）；
少数调用点历史上用自己模块级的冻结绑定（如 app.comfyui），显式传
base_dir 保持原语义。
"""
from pathlib import Path
from typing import FrozenSet, Optional
from urllib.parse import urlsplit

MANAGED_PREFIX = "/outputs/"

IMAGE_SUFFIXES: FrozenSet[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
)
VIDEO_SUFFIXES: FrozenSet[str] = frozenset(
    {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
)
AUDIO_SUFFIXES: FrozenSet[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
)
MEDIA_SUFFIXES: FrozenSet[str] = IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES


def _relative_segment(url: str) -> Optional[str]:
    """从受管 URL 取出 /outputs/ 之后的相对段；不受管/可疑返回 None。"""
    u = (url or "").strip()
    if not u:
        return None
    try:
        # 去掉 query/fragment（受管 URL 由本服务生成，不应携带查询串）
        u = urlsplit(u).path
    except ValueError:
        return None
    if not u:
        return None
    idx = 0 if u.startswith(MANAGED_PREFIX) else u.find(MANAGED_PREFIX)
    if idx < 0:
        return None
    rel = u[idx + len(MANAGED_PREFIX):]
    if not rel:
        return None
    # 可疑编码：受管文件名由本服务生成（aivideo_00001_.png 等），出现 %
    # 一律视为编码穿越尝试（%2e%2e%2f → ../）
    if "%" in rel:
        return None
    # 反斜杠在 Windows 路径语义下是分隔符，URL 里出现即异常
    if "\\" in rel:
        return None
    # 控制字符/空白（含 NUL）
    if any(ord(ch) < 32 or ch.isspace() for ch in rel):
        return None
    rel = rel.lstrip("/")  # /outputs//x → x（仍受下面边界校验约束）
    if not rel:
        return None
    # . / .. 路径段（父目录跳转的第一道闸）
    if any(seg in (".", "..") for seg in rel.split("/")):
        return None
    return rel


def _default_base() -> Path:
    """默认受管根：调用时动态读 app.config（测试 monkeypatch config 即生效）。"""
    from . import config

    return config.OUTPUTS_DIR


def resolve_managed_asset(
    url: str,
    allowed_types: Optional[FrozenSet[str]] = IMAGE_SUFFIXES,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """受管 URL → 本机安全路径；不受管/越界/类型不符返回 None。

    allowed_types：扩展名白名单（小写带点，如 {".png", ".jpg"}）；
    传 None 表示不限类型（仍做边界校验）。
    base_dir：受管根目录；默认动态读 app.config.OUTPUTS_DIR
    （调用点历史上用自己模块级的 OUTPUTS_DIR 时显式传入）。
    """
    rel = _relative_segment(url)
    if rel is None:
        return None
    base = Path(base_dir) if base_dir is not None else _default_base()
    # 盘符/绝对路径注入：pathlib 在 Windows 下会把 "C:/x" 这类片段当作
    # 绝对路径替换掉 base——先拦一道，realpath 边界再做兜底
    if Path(rel).is_absolute() or Path(rel).drive:
        return None
    candidate = base / rel
    try:
        resolved = candidate.resolve(strict=False)
        base_resolved = base.resolve(strict=False)
    except OSError:
        return None
    # realpath 边界：../ 逃逸与符号链接指向目录外都在这里被拒
    if not resolved.is_relative_to(base_resolved):
        return None
    if allowed_types:
        if resolved.suffix.lower() not in allowed_types:
            return None
    return resolved
