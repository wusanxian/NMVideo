"""任务队列：单 worker 顺序执行，SQLite 持久化，支持取消与进度回调"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import db as db_module
from . import hw_probe
from . import storyboard as sb
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
        """崩溃恢复（重启后重启队列，而不是一刀切标失败）：

        - pending：还没开始跑的任务**重新入队**（按提交顺序 FIFO），
          批量生成的排队任务不丢。
        - running：worker 线程已随上次进程死亡，本地引擎没有断点续算；
          ComfyUI 任务也无法从我们的 job_id 反查其内部 prompt 历史，
          因此统一标记中断并**明确告知用户需重新生成**，对应镜头打回需重做。
        """
        # ---- running：无法续算，标记中断 ----
        running = db_module.db.list_jobs(limit=1000, status="running")
        for job in running:
            db_module.db.update_job(
                job["id"],
                status="failed",
                error="生成在服务重启时中断（不支持断点续算），请重新生成",
                stage="已中断",
                finished_at=_utcnow(),
            )
            shot_id = job.get("shot_id") or ""
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note="服务重启时生成中断，请重新生成")
                sb.set_shot_status(shot_id, "rework")
        if running:
            print(f"[queue] 标记了 {len(running)} 个中断任务（服务重启前正在生成）")

        # ---- pending：重新入队（保持提交顺序） ----
        pending = db_module.db.list_jobs(limit=1000, status="pending", order="asc")
        for job in pending:
            self._queue.put_nowait(job["id"])
            shot_id = job.get("shot_id") or ""
            if shot_id and sb.get_shot(shot_id):
                sb.set_shot_status(shot_id, "queued")
        if pending:
            print(f"[queue] 重新入队 {len(pending)} 个未执行任务")

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

        # 分镜工作台联动：该 job 若由分镜发起，全程同步镜头状态
        shot_id = job.get("shot_id") or ""

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
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note=f"引擎加载失败: {e}"[:200])
                sb.set_shot_status(shot_id, "rework")
            return

        db_module.db.update_job(job_id, status="running", stage="准备中", progress=0.0)
        started = time.time()

        if shot_id and sb.get_shot(shot_id):
            sb.set_shot_status(shot_id, "generating")

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
                    reference_image=job.get("reference_image") or "",
                ),
            )
        except GenerationCancelled:
            db_module.db.update_job(
                job_id, status="cancelled", stage="已取消", finished_at=_utcnow()
            )
            self._cancelled.discard(job_id)
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note="已取消")
                sb.set_shot_status(shot_id, "draft")
            return
        except Exception as e:
            db_module.db.update_job(
                job_id, status="failed", error=str(e), stage="失败", finished_at=_utcnow()
            )
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note=str(e)[:200])
                sb.set_shot_status(shot_id, "rework")
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
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note=f"视频合成失败: {e}"[:200])
                sb.set_shot_status(shot_id, "rework")
            return

        poster = frames_dir / "frame_0000.png"
        video_url = f"/outputs/{job_id}.mp4"
        poster_url = f"/outputs/{job_id}/frames/frame_0000.png" if poster.exists() else None
        db_module.db.update_job(
            job_id,
            status="done",
            progress=1.0,
            stage="预览完成" if job.get("preview") else "已完成",
            video_url=video_url,
            poster_url=poster_url,
            elapsed_s=round(time.time() - started, 1),
            finished_at=_utcnow(),
        )
        # 分镜工作台联动：落一个候选版本，镜头进入待审核
        if shot_id and sb.get_shot(shot_id):
            done_job = db_module.db.get_job(job_id)
            version = sb.create_version(shot_id, done_job, video_url, poster_url or "")
            sb.update_shot(
                shot_id, current_version_id=version["id"], job_id="", note=""
            )
            sb.set_shot_status(shot_id, "review")
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
