"""任务队列：单 worker 顺序执行，SQLite 持久化，支持取消与进度回调"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import db as db_module
from . import hw_probe
from .config import OUTPUTS_DIR
from .engines import get_engine
from .engines.base import GenerationCancelled
from .video import frames_to_video


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobQueue:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancelled: set = set()
        self._task = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gen")
        self._engine_instances: dict = {}

    # ---------- 生命周期 ----------
    def start(self):
        self._recover_stale_jobs()
        if self._task is None:
            self._task = asyncio.create_task(self._worker())

    def _recover_stale_jobs(self):
        """崩溃恢复：上次服务中断时处于 running/pending 的任务标记为失败，
        避免前端看到永远卡住的僵尸任务。"""
        stale = db_module.db.list_jobs(limit=1000, status="running")
        stale += db_module.db.list_jobs(limit=1000, status="pending")
        for job in stale:
            db_module.db.update_job(
                job["id"],
                status="failed",
                error="服务重启前中断（自动恢复）",
                stage="失败",
                finished_at=_utcnow(),
            )
        if stale:
            print(f"[queue] 清理了 {len(stale)} 个中断任务")

    def stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._executor.shutdown(wait=False)

    # ---------- 外部操作 ----------
    async def submit(self, params: dict) -> str:
        job = db_module.db.create_job(params)
        await self._queue.put(job["id"])
        return job["id"]

    def cancel(self, job_id: str):
        job = db_module.db.get_job(job_id)
        if not job or job["status"] not in ("pending", "running"):
            return
        self._cancelled.add(job_id)
        if job["status"] == "pending":
            # 还没开始跑，直接标记取消，worker 取到后会跳过
            db_module.db.update_job(
                job_id, status="cancelled", stage="已取消", finished_at=_utcnow()
            )

    # ---------- worker ----------
    async def _worker(self):
        while True:
            job_id = await self._queue.get()
            try:
                if job_id in self._cancelled:
                    # 排队阶段已被取消
                    self._cancelled.discard(job_id)
                    continue
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # worker 不能死
                db_module.db.update_job(
                    job_id, status="failed", error=f"worker 异常: {e}",
                    stage="失败", finished_at=_utcnow(),
                )

    async def _run_job(self, job_id: str):
        job = db_module.db.get_job(job_id)
        if not job:
            return

        # 引擎实例缓存（模型常驻内存，切任务不重载）
        engine_id = job["engine"]
        try:
            if engine_id not in self._engine_instances:
                self._engine_instances[engine_id] = get_engine(engine_id)()
            engine = self._engine_instances[engine_id]
        except Exception as e:
            db_module.db.update_job(
                job_id, status="failed", error=f"引擎加载失败: {e}",
                stage="失败", finished_at=_utcnow(),
            )
            return

        db_module.db.update_job(job_id, status="running", stage="准备中", progress=0.0)
        started = time.time()

        frames_dir = OUTPUTS_DIR / job_id / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        def on_progress(p: float, stage: str):
            db_module.db.update_job(
                job_id, progress=round(min(max(p, 0.0), 1.0), 4), stage=stage
            )

        def is_cancelled() -> bool:
            return job_id in self._cancelled

        loop = asyncio.get_running_loop()
        try:
            frame_paths = await loop.run_in_executor(
                self._executor,
                lambda: engine.generate(
                    prompt=job["prompt"],
                    negative_prompt=job["negative_prompt"] or "",
                    width=job["width"],
                    height=job["height"],
                    num_frames=job["num_frames"],
                    fps=job["fps"],
                    steps=job["steps"],
                    guidance=job["guidance"],
                    seed=job["seed"],
                    frames_dir=frames_dir,
                    on_progress=on_progress,
                    is_cancelled=is_cancelled,
                ),
            )
        except GenerationCancelled:
            db_module.db.update_job(
                job_id, status="cancelled", stage="已取消", finished_at=_utcnow()
            )
            self._cancelled.discard(job_id)
            return
        except Exception as e:
            db_module.db.update_job(
                job_id, status="failed", error=str(e), stage="失败", finished_at=_utcnow()
            )
            return

        # 合成 MP4（参数写入元数据，画廊可读回复用）
        db_module.db.update_job(job_id, stage="合成视频", progress=0.92)
        try:
            await loop.run_in_executor(
                self._executor,
                lambda: frames_to_video(
                    frames_dir,
                    OUTPUTS_DIR / f"{job_id}.mp4",
                    job["fps"],
                    metadata=self._job_metadata(job, engine),
                ),
            )
        except Exception as e:
            db_module.db.update_job(
                job_id, status="failed", error=f"视频合成失败: {e}",
                stage="失败", finished_at=_utcnow(),
            )
            return

        poster = frames_dir / "frame_0000.png"
        db_module.db.update_job(
            job_id,
            status="done",
            progress=1.0,
            stage="预览完成" if job.get("preview") else "已完成",
            video_url=f"/outputs/{job_id}.mp4",
            poster_url=f"/outputs/{job_id}/frames/frame_0000.png" if poster.exists() else None,
            elapsed_s=round(time.time() - started, 1),
            finished_at=_utcnow(),
        )
        self._cancelled.discard(job_id)

    @staticmethod
    def _job_metadata(job: dict, engine) -> dict:
        """嵌入 MP4 的生成参数（FreeVideo 式视频自带工作流）"""
        hw = hw_probe.probe().to_dict()
        return {
            "app": "aivideo",
            "engine": job["engine"],
            "prompt": job["prompt"],
            "negative_prompt": job.get("negative_prompt") or "",
            "width": job["width"],
            "height": job["height"],
            "num_frames": job["num_frames"],
            "fps": job["fps"],
            "steps": job["steps"],
            "guidance": job["guidance"],
            "seed": job["seed"],
            "preset": job.get("preset") or "",
            "preview": bool(job.get("preview")),
            "hardware": f"{hw['gpu_name']} | {hw['quant']} | {hw['offload']}",
        }


# 全局单例：API 路由与 FastAPI lifespan 共用同一个队列
job_queue = JobQueue()
