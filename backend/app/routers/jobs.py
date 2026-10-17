"""任务相关 REST API"""
import random
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db as db_module
from .. import storyboard as sb
from ..config import OUTPUTS_DIR
from ..engines import ENGINES
from ..quality import PREVIEW_OVERRIDE, QUALITY_PRESETS, apply_preset
from ..queue_manager import job_queue
from ..video import read_video_metadata

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    engine: str = "anime"
    prompt: str = Field(..., min_length=1, max_length=4000)
    negative_prompt: str = ""
    # 范围覆盖 Wan 2.2（720p / 121 帧）；各引擎内部按自身约束吸附
    width: int = Field(512, ge=256, le=1344)
    height: int = Field(512, ge=256, le=768)
    num_frames: int = Field(16, ge=8, le=121)
    fps: int = Field(8, ge=4, le=30)
    steps: int = Field(25, ge=5, le=100)
    guidance: float = Field(7.5, ge=1.0, le=20.0)
    seed: int = Field(-1)  # -1 表示随机
    preset: str = ""       # lite / standard / fine / ultra（空=自定义参数）
    preview: bool = False  # 两遍采样第一遍：半分辨率快速预览


@router.post("", status_code=201)
async def create_job(data: JobCreate):
    if data.engine not in ENGINES:
        raise HTTPException(status_code=400, detail=f"未知引擎: {data.engine}")
    params = data.model_dump()                          # 默认值
    explicit = data.model_dump(exclude_unset=True)      # 用户显式传入的字段

    # 质量档位 → 具体参数（档位覆盖默认值）
    preset_id = params.get("preset") or explicit.get("preset")
    if preset_id:
        if preset_id not in QUALITY_PRESETS:
            raise HTTPException(status_code=400, detail=f"未知档位: {preset_id}")
        params = apply_preset(params, preset_id)

    # 显式传入的参数再覆盖档位
    for k, v in explicit.items():
        if k != "preset":
            params[k] = v

    # 预览模式：强制轻量预览参数（两遍采样第一遍）
    if params.get("preview"):
        params.update(PREVIEW_OVERRIDE)

    if params["seed"] < 0:
        params["seed"] = random.randint(0, 2**31 - 1)
    job_id = await job_queue.submit(params)
    return db_module.db.get_job(job_id)


@router.get("")
def list_jobs(status: Optional[str] = None, limit: int = 100):
    return db_module.db.list_jobs(limit=limit, status=status)


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
