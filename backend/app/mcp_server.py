"""NMVideo 内置 MCP 服务：让外部 AI 助手（WorkBuddy / Claude Code 等）直连出片。

对齐 FreeVideo v0.4.0 的「AI Agent 独立生成视频」形态：
- 应用在 /mcp 暴露 MCP Streamable HTTP 端点；
- 「模型服务」页展示连接地址 + 访问令牌，用户复制粘到 AI 客户端即可对话操控；
- 首批工具只做出片闭环：状态 / 生成 / 列表 / 查询 / 取消。

工具实现直接复用 REST 路由函数（行为与 UI 完全一致，参数校验/入队冻结/
引擎吸附全部同源），不绕第二套生成逻辑。
"""
import json
import secrets
import urllib.parse
from typing import Optional

from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse

from . import db as db_module
from .engines import ENGINES
from .routers.jobs import (
    JobCreate,
    cancel_job as _api_cancel_job,
    create_job as _api_create_job,
    get_job as _api_get_job,
    list_jobs as _api_list_jobs,
)
from .settings import get_section, update_settings

# ---------------------------------------------------------------- 设置与令牌

_SETTINGS_KEY = "mcp"


def get_state() -> dict:
    """MCP 接入状态（token 打码，完整值走 reveal）。"""
    sec = get_section(_SETTINGS_KEY)
    enabled = bool(sec.get("enabled"))
    token = sec.get("token", "")
    masked = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else ("已设置" if token else "")
    return {"enabled": enabled, "token_masked": masked, "has_token": bool(token),
            "endpoint_path": "/mcp"}


def reveal_token() -> str:
    """返回完整 token（供前端「复制地址」用；本机 UI 场景）。"""
    return _ensure_token()


def set_enabled(enabled: bool, regenerate: bool = False) -> dict:
    """启用/停用 MCP 端点；启用时确保存在 token，可强制换新。"""
    patch = {_SETTINGS_KEY: {"enabled": bool(enabled)}}
    if regenerate:
        patch[_SETTINGS_KEY]["token"] = secrets.token_hex(16)
    update_settings(patch)
    if enabled and not regenerate:
        _ensure_token()
    return get_state()


def _ensure_token() -> str:
    sec = get_section(_SETTINGS_KEY)
    token = sec.get("token", "")
    if not token:
        token = secrets.token_hex(16)
        update_settings({_SETTINGS_KEY: {"token": token}})
    return token


# ---------------------------------------------------------------- MCP 服务与工具

mcp = MCPServer("NMVideo")

_DEAFULT_FPS = 16  # duration_seconds -> 帧数的换算基准（引擎内部会按自身约束吸附）


def _err(exc: Exception) -> dict:
    """路由函数抛 HTTPException 时转成 agent 可读的错误 dict。"""
    detail = getattr(exc, "detail", str(exc))
    return {"error": str(detail)}


@mcp.tool()
async def app_status() -> dict:
    """NMVideo 应用状态总览：各视频引擎可用性、模型服务（LLM/图像）配置情况、队列概况。

    生成视频前先调用本工具确认引擎可用；不可用时会说明原因（缺显卡/缺模型/ComfyUI 未启动）。
    """
    engines = {}
    for eid, eng in ENGINES.items():
        try:
            d = eng.describe()
            engines[eid] = {"name": d.get("name", eid), "status": d.get("status"),
                            "note": d.get("note", "")}
        except Exception as exc:  # noqa: BLE001
            engines[eid] = {"name": eid, "status": "unavailable", "note": str(exc)}
    jobs = db_module.db.list_jobs(limit=200)
    counts = {}
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    sec = get_section("llm") or {}
    return {
        "app": "NMVideo",
        "engines": engines,
        "queue": {"running": counts.get("running", 0), "pending": counts.get("pending", 0),
                  "done_total": counts.get("done", 0), "failed_total": counts.get("failed", 0)},
        "llm_configured": bool(sec.get("base_url")),
        "hint": "用 create_video 生成视频；用 get_job 轮询进度；完成后 video_url 即成片地址。",
    }


@mcp.tool()
async def create_video(
    prompt: str,
    duration_seconds: Optional[float] = None,
    preset: str = "",
    engine: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    num_frames: Optional[int] = None,
    fps: Optional[int] = None,
    seed: int = -1,
    preview: bool = False,
) -> dict:
    """创建视频生成任务（入队后立即返回任务 id，不等待生成完成）。

    Args:
        prompt: 画面描述（支持中文，建议包含主体/动作/场景/镜头语言）。
        duration_seconds: 期望时长（秒），不填用引擎默认帧数；换算按 16fps，
            各引擎内部会按自身约束吸附（如 H3 为 24fps、5~15 秒）。
        preset: 质量档位 lite/standard/fine/ultra，空 = 按传入参数自定义。
            不确定时推荐 \"standard\"。
        engine: 引擎 id（见 app_status 的 engines 键名），不填用应用默认引擎。
        width/height: 画布尺寸（px），不填用应用默认。
        num_frames/fps: 直接指定帧数与帧率（给了 duration_seconds 时优先用这个）。
        seed: 随机种子，-1 = 随机。
        preview: true = 两遍采样的快速预览（半分辨率，先看效果再转正）。

    Returns:
        {job_id, status, ...}；之后用 get_job(job_id) 轮询进度。
    """
    if not prompt or not prompt.strip():
        return {"error": "prompt 不能为空"}
    params = {"prompt": prompt.strip(), "seed": seed, "preview": preview}
    if preset:
        params["preset"] = preset
    if engine:
        params["engine"] = engine
    if width:
        params["width"] = int(width)
    if height:
        params["height"] = int(height)
    if num_frames:
        params["num_frames"] = int(num_frames)
        params["fps"] = int(fps or _DEAFULT_FPS)
    elif duration_seconds:
        # REST 层 num_frames 上限 121（约 7.5 秒@16fps）；超出先钳到上限，
        # 各引擎内部会按自身约束吸附/重采样到目标时长
        params["num_frames"] = min(121, max(8, round(float(duration_seconds) * _DEAFULT_FPS)))
        params["fps"] = _DEAFULT_FPS
    try:
        job = await _api_create_job(JobCreate(**params))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return {"job_id": job["id"], "status": job["status"], "engine": job["engine"],
            "prompt": job["prompt"],
            "hint": "已入队。用 get_job 轮询；本地浏览器可打开 video_url 观看成片。"}


@mcp.tool()
async def list_jobs(status: Optional[str] = None, limit: int = 20) -> dict:
    """列出最近的生成任务（默认 20 条，新→旧）。

    Args:
        status: 过滤状态 pending/running/done/failed/cancelled，不填返回全部。
        limit: 返回条数上限。
    """
    try:
        rows = _api_list_jobs(status=status, limit=max(1, min(int(limit), 100)))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return {"jobs": [
        {"job_id": r["id"], "status": r["status"], "progress": round(r.get("progress") or 0, 2),
         "stage": r.get("stage", ""), "engine": r.get("engine", ""),
         "prompt": (r.get("prompt") or "")[:80], "video_url": r.get("video_url") or None}
        for r in rows
    ]}


@mcp.tool()
async def get_job(job_id: str) -> dict:
    """查询任务详情：状态、进度、当前阶段；完成时返回 video_url（成片地址）。

    Args:
        job_id: create_video 返回的任务 id。
    """
    try:
        job = _api_get_job(job_id)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    out = {
        "job_id": job["id"], "status": job["status"],
        "progress": round(job.get("progress") or 0, 2), "stage": job.get("stage", ""),
        "engine": job.get("engine", ""), "prompt": job.get("prompt", ""),
        "video_url": job.get("video_url") or None,
        "error": job.get("error") or None,
    }
    if job["status"] == "done":
        out["hint"] = "生成完成。video_url 是应用本机相对地址，拼上应用根地址即可访问。"
    elif job["status"] in ("pending", "running"):
        out["hint"] = "仍在生成中，可稍后再次调用本工具。视频生成通常需要数分钟。"
    elif job["status"] == "failed":
        out["hint"] = "生成失败。可把 error 内容转告用户，或调整参数后重新 create_video。"
    return out


@mcp.tool()
async def cancel_job(job_id: str) -> dict:
    """取消一个排队中/生成中的任务。

    Args:
        job_id: 任务 id。
    """
    try:
        _api_cancel_job(job_id)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return {"ok": True, "job_id": job_id,
            "hint": "已请求取消。注意：部分引擎（如 H3 原生推理）在去噪阶段无法即时中断。"}


# ---------------------------------------------------------------- 鉴权与挂载

class _TokenGuard:
    """ASGI 中间件：校验 ?token= 或 Authorization: Bearer；未启用时拒绝。"""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        sec = get_section(_SETTINGS_KEY)
        if not sec.get("enabled"):
            return await JSONResponse(
                {"error": "MCP 服务未启用。请在 NMVideo「模型服务」页开启 AI 助手接入。"},
                status_code=403,
            )(scope, receive, send)
        expected = sec.get("token", "")
        qs = urllib.parse.parse_qs(scope.get("query_string", b"").decode("utf-8", "ignore"))
        supplied = (qs.get("token") or [None])[0]
        if not supplied:
            for k, v in scope.get("headers") or []:
                if k == b"authorization" and v.lower().startswith(b"bearer "):
                    supplied = v[7:].decode("utf-8", "ignore").strip()
        if not expected or not supplied or not secrets.compare_digest(supplied, expected):
            return await JSONResponse(
                {"error": "MCP token 无效。请在「模型服务」页复制完整连接地址。"},
                status_code=401,
            )(scope, receive, send)
        await self.inner(scope, receive, send)


def build_mcp_asgi_app():
    """返回带 token 守卫的 MCP ASGI 应用（挂到 /mcp，精确 Route）。"""
    return _TokenGuard(_RAW_APP)


_RAW_APP = mcp.streamable_http_app()  # 内层 Starlette 应用（路由在 /mcp）


def mcp_lifespan():
    """内层 MCP 应用的 lifespan（初始化 task group），由主应用 lifespan 链入。"""
    return _RAW_APP.router.lifespan_context(_RAW_APP)
