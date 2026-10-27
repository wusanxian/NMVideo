"""FastAPI 应用入口"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import hw_probe
from .db import DatabaseBusyError
from .queue_manager import job_queue
from .quality import list_presets
from .routers import assets as assets_router
from .routers import engines as engines_router
from .routers import jobs as jobs_router
from .routers import launch as launch_router
from .routers import novel as novel_router
from .routers import novel_bible as novel_bible_routers
from .routers import novel_script as novel_script_routers
from .routers import novel_shots as novel_shots_routers
from .routers import novel_submit as novel_submit_routers
from .routers import services as services_router
from .routers import storyboard as storyboard_router
from .config import FRONTEND_DIST, OUTPUTS_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    # PR-N05：登记主事件循环（小说提交器跨线程创建生成任务用）
    from . import novel_submit
    novel_submit.register_loop(asyncio.get_running_loop())
    job_queue.start()
    # MCP 子应用（/mcp）有自己的 lifespan（初始化 StreamableHTTP task group），
    # Starlette 不会自动运行挂载子应用的 lifespan，这里手动链入
    from .mcp_server import mcp_lifespan
    async with mcp_lifespan():
        yield
    job_queue.stop()


app = FastAPI(title="AI 视频工坊", version="0.2.0", lifespan=lifespan)

# 数据库忙（外部写者竞争）：可理解、可重试的 503，不无限重试、不重放写请求
@app.exception_handler(DatabaseBusyError)
async def _db_busy_handler(_request, exc: DatabaseBusyError):
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "2"},
        content={"detail": str(exc)},
    )

# 开发模式（vite dev server 5173）跨源
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",  # 桌面壳标题栏（file:// 页面）调 /api/services/mcp* 用
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(engines_router.router)
app.include_router(jobs_router.router)
app.include_router(launch_router.router)
app.include_router(novel_router.router)
app.include_router(novel_bible_routers.router)
app.include_router(novel_bible_routers.bible_router)
app.include_router(novel_script_routers.router)
app.include_router(novel_script_routers.ep_router)
app.include_router(novel_shots_routers.router)
app.include_router(novel_shots_routers.sp_router)
app.include_router(novel_submit_routers.sp_router)
app.include_router(storyboard_router.router)
app.include_router(assets_router.router)
app.include_router(services_router.router)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

# MCP 端点（外部 AI 助手接入）：Route 精确匹配 /mcp，
# 必须在 SPA catch-all 之前注册（Mount("/mcp") 不匹配裸路径，会被 catch-all 抢走）
from starlette.routing import Route  # noqa: E402

from .mcp_server import build_mcp_asgi_app  # noqa: E402

app.router.routes.append(
    Route("/mcp", build_mcp_asgi_app(), methods=["GET", "POST", "DELETE"])
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/hardware")
def hardware():
    """硬件探测结果与 Wan 引擎推荐配置"""
    return hw_probe.probe().to_dict()


@app.get("/api/presets")
def presets():
    """质量档位列表"""
    return list_presets()

# 生产模式：前端构建产物由后端统一托管（单端口 8000）
# 注意：SPA catch-all 必须最后注册，否则会覆盖 /api/* 路由
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        return FileResponse(str(FRONTEND_DIST / "index.html"))
