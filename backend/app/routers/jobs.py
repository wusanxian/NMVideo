"""任务相关 REST API"""
import random
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db as db_module
from .. import resource_guard
from .. import storyboard as sb
from ..config import OUTPUTS_DIR
from ..engines import DEFAULT_ENGINE_ID, ENGINES, is_comfy_engine
from ..generation import prepare_comfy_workflow, persist_prepared_job
from ..comfyui import ComfyUIError
from ..quality import normalize_generation
from ..queue_manager import job_queue
from ..video import read_video_metadata
from ..upgrades import prepare_upgrade

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    # 默认引擎 = ComfyUI 通道（架构口径：ComfyUI 是唯一视频生成底座）
    engine: str = DEFAULT_ENGINE_ID
    prompt: str = Field(..., min_length=1, max_length=4000)
    negative_prompt: str = ""
    # PR-08 后半：多参考图（额外参考图 URL 数组，≤4 张；normalize 校验后冻结为 JSON）
    reference_images: Optional[list] = None
    # 范围覆盖 Wan 2.2 与 MiniMax H3（720p 竖屏 / 1344×768 画布）；各引擎内部按自身约束吸附
    width: int = Field(512, ge=256, le=1344)
    height: int = Field(512, ge=256, le=1344)
    num_frames: int = Field(16, ge=8, le=121)
    fps: int = Field(8, ge=4, le=30)
    steps: int = Field(25, ge=5, le=100)
    guidance: float = Field(7.5, ge=1.0, le=20.0)
    seed: int = Field(-1)  # -1 表示随机
    preset: str = ""       # lite / standard / fine / ultra（空=自定义参数）
    preview: bool = False  # 两遍采样第一遍：半分辨率快速预览


class JobUpgrade(BaseModel):
    preset: str = ""


@router.post("", status_code=201)
async def create_job(data: JobCreate):
    if data.engine not in ENGINES:
        raise HTTPException(status_code=400, detail=f"未知引擎: {data.engine}")
    try:
        params = normalize_generation(data.model_dump(), data.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if params["seed"] < 0:
        params["seed"] = random.randint(0, 2**31 - 1)

    # 资源守卫：入队前就按「本任务要多少 vs 本机剩多少」定档。降档必须发生在
    # 固化工作流快照之前，否则快照里的分辨率与任务参数不一致。
    decision = resource_guard.plan(
        params["engine"], params, comfy_url=params.get("comfy_url") or "",
    )
    if not decision.ok:
        raise HTTPException(409, decision.reason)
    params = decision.params
    if decision.note:
        params["resource_note"] = decision.note

    graph, template = None, None
    try:
        # ComfyUI 通道（默认引擎，以及未来任何声明 execution_channel=comfyui 的
        # 引擎）都必须在入队前固化工作流快照；兜底通道不产生 ComfyUI 任务。
        if is_comfy_engine(params["engine"]):
            graph, template = prepare_comfy_workflow(params, output_dir=OUTPUTS_DIR)
        job = persist_prepared_job(db_module.db, params, OUTPUTS_DIR, graph=graph, template=template)
    except (ValueError, ComfyUIError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "工作流快照保存失败，未创建生成任务") from exc
    await job_queue.enqueue(job["id"])
    return job


@router.get("")
def list_jobs(status: Optional[str] = None, limit: int = 100):
    return db_module.db.list_jobs(limit=limit, status=status)


@router.post("/{job_id}/upgrade", status_code=201)
async def upgrade_job(job_id: str, data: JobUpgrade = None):
    source = db_module.db.get_job(job_id)
    if not source:
        raise HTTPException(404, "原任务不存在")
    shot_id = source.get("shot_id") or ""
    if shot_id:
        shot = sb.get_shot(shot_id)
        if not shot:
            raise HTTPException(409, "原镜头已删除，无法创建镜头候选")
        if shot["status"] in ("queued", "generating"):
            raise HTTPException(409, "该镜头已有生成任务，请等待完成")
    try:
        params, graph, template = prepare_upgrade(source, data.preset if data else "", output_dir=OUTPUTS_DIR)
        job = persist_prepared_job(db_module.db, params, OUTPUTS_DIR, graph=graph, template=template)
    except (ValueError, ComfyUIError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "升级快照保存失败，未创建生成任务") from exc
    if shot_id:
        sb.set_shot_status(shot_id, "queued", job_id=job["id"], review_stage="generation")
    await job_queue.enqueue(job["id"])
    return job


@router.get("/{job_id}")
def get_job(job_id: str):
    job = db_module.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    job = db_module.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    job_queue.cancel(job_id)
    return {"ok": True}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str):
    """断线待核查（needs_attention）任务的手动重试：重新入队走 resume。

    仅 needs_attention 可重试——该状态的任务从未被 worker 执行过
    （HF-04：ComfyUI 暂不可达时重启恢复放入待核查），重新入队安全；
    其他状态的重试语义未定义（要重来请取消后重新生成）。
    ComfyUI 仍不可达时会以明确错误失败，可稍后再次重试。
    """
    job = db_module.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job["status"] != "needs_attention":
        raise HTTPException(
            status_code=409,
            detail=f"仅「待核查」状态的任务可重试（当前: {job['status']}）",
        )
    db_module.db.update_job(
        job_id, status="running", stage="重试：恢复等待（手动）",
        progress=0.0, error="",
    )
    await job_queue.enqueue(job_id)
    shot_id = job.get("shot_id") or ""
    if shot_id and sb.get_shot(shot_id):
        sb.set_shot_status(shot_id, "generating", review_stage="generation")
    return db_module.db.get_job(job_id)


@router.delete("/{job_id}")
def delete_job(job_id: str):
    job = db_module.db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 3A：任务产物被镜头版本/时间线引用时拒绝删除（409 + 引用明细），
    # 避免画廊误删正在剪辑使用的视频
    refs = sb.job_references(job_id)
    if refs["versions"] or refs["timeline_items"]:
        raise HTTPException(status_code=409, detail={
            "message": "该任务的产物已被镜头版本/时间线引用，不能删除；"
                       "请先删除对应镜头版本或时间线片段",
            "references": refs,
        })
    if job["status"] == "running":
        raise HTTPException(409, "任务正在执行，请先取消并等待停止后再删除")
    if job["status"] in ("pending", "needs_attention"):
        job_queue.cancel(job_id)
        # 与 worker 认领竞争时，等待它结束，不能删掉执行中的目录和数据库行。
        latest = db_module.db.get_job(job_id)
        if latest and latest["status"] == "running":
            raise HTTPException(409, "任务正在停止，请等待停止后再删除")
    shutil.rmtree(OUTPUTS_DIR / job_id, ignore_errors=True)
    (OUTPUTS_DIR / f"{job_id}.mp4").unlink(missing_ok=True)
    db_module.db.delete_job(job_id)
    return {"ok": True}


@router.get("/{job_id}/metadata")
def get_job_metadata(job_id: str):
    """读回 MP4 内嵌的生成参数（视频自带工作流）"""
    video = OUTPUTS_DIR / f"{job_id}.mp4"
    if not video.exists():
        raise HTTPException(status_code=404, detail="视频不存在")
    return read_video_metadata(video)
