"""FastAPI 应用入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import hw_probe
from .queue_manager import job_queue
from .quality import list_presets
from .routers import engines as engines_router
from .routers import jobs as jobs_router
from .config import FRONTEND_DIST, OUTPUTS_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    job_queue.start()
    yield
    job_queue.stop()


app = FastAPI(title="AI 视频工坊", version="0.2.0", lifespan=lifespan)

# 开发模式（vite dev server 5173）跨源
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(engines_router.router)
app.include_router(jobs_router.router)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


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
