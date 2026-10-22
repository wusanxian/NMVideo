"""任务队列：单 worker 顺序执行，SQLite 持久化，支持取消与进度回调"""
import asyncio
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import db as db_module
from . import hw_probe
from . import storyboard as sb
from .config import OUTPUTS_DIR
from .engines import get_engine, is_comfy_engine
from .engines.base import GenerationCancelled
from .video import extract_first_frame, frames_to_video, remux_video


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobQueue:
    # HF-04：恢复扫描分页大小（原来 limit=1000 硬截断，pending 过多会丢任务）
    _RECOVER_PAGE = 200

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

    def _iter_jobs(self, status: str, order: str = "desc"):
        """分页拉取某状态的全部任务（HF-04：>1000 pending/running 也完整恢复）。

        原来 `list_jobs(limit=1000)` 一次截断，超量任务在重启恢复时被静默
        遗漏（既不重跑也不标失败，成为永久 pending 僵尸）。
        """
        cursor = None
        while True:
            batch = db_module.db.list_jobs(
                limit=self._RECOVER_PAGE, status=status, order=order, after=cursor
            )
            if not batch:
                return
            yield from batch
            if len(batch) < self._RECOVER_PAGE:
                return
            cursor = (batch[-1]["created_at"], batch[-1]["id"])

    def _recover_stale_jobs(self):
        """崩溃恢复（重启后重启队列，而不是一刀切标失败）：

        - pending：还没开始跑的任务**重新入队**（按提交顺序 FIFO，分页拉取），
          批量生成的排队任务不丢。
        - running：worker 线程已随上次进程死亡。
          · ComfyUI 任务（已持久化 prompt_id）：先查 ComfyUI 历史/队列——
            还在跑/排队/已完成 → 重新入队走 resume 路径（跳过提交，
            按 prompt_id 继续等并下载产物）；ComfyUI 侧也没了 → 标失败。
          · ComfyUI **暂不可达**（HF-04：断网/服务重启中）→ 标
            needs_attention「待核查」：不重新提交、不过早确认失败，
            下次重启或用户手动重试时再探。
          · 本地引擎没有断点续算：统一标记中断并**明确告知用户需重新生成**，
            对应镜头打回需重做。
        - needs_attention：上次重启时 ComfyUI 不可达的任务——再探一次，
          恢复了就重新入队；确认没了（error/lost）才标失败；仍不可达则
          继续待核查，绝不误判。
        """
        recovered, interrupted, attention = [], [], []

        def _fail_job(job: dict, error: str, note: str) -> None:
            db_module.db.update_job(
                job["id"], status="failed", error=error,
                stage="失败", finished_at=_utcnow(),
            )
            shot_id = job.get("shot_id") or ""
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note=note)
                # 3A：回到需重做时清除视频审核标记（版本未变，无待审成片）
                sb.set_shot_status(shot_id, "rework", review_stage="")

        # ---- running：ComfyUI 先探活再决定，其余标记中断 ----
        for job in self._iter_jobs("running"):
            shot_id = job.get("shot_id") or ""
            prompt_id = job.get("prompt_id") or ""
            if is_comfy_engine(job.get("engine")) and prompt_id:
                state = self._probe_comfy_job(prompt_id, self._comfy_url(job))
                if state in ("done", "running", "queued"):
                    # ComfyUI 侧任务仍在（或已完成）：重新入队，worker 走
                    # resume 路径（resume_prompt_id）直接等待/下载
                    db_module.db.update_job(
                        job["id"], stage="恢复等待（服务重启）", progress=0.0,
                    )
                    self._queue.put_nowait(job["id"])
                    if shot_id and sb.get_shot(shot_id):
                        sb.set_shot_status(shot_id, "generating",
                                           review_stage="generation")
                    recovered.append(job["id"])
                    continue
                if state == "temporarily_unavailable":
                    # HF-04：ComfyUI 暂不可达 ≠ 任务不存在。待核查：不重新
                    # 提交、不标失败；镜头保持 generating（不是真的失败），
                    # 下次重启自动再探，用户也可在任务卡片手动重试。
                    db_module.db.update_job(
                        job["id"], status="needs_attention",
                        stage="待核查：ComfyUI 暂不可达",
                        error="重启恢复时 ComfyUI 暂不可达，未确认任务状态；"
                              "ComfyUI 恢复后会自动继续，也可手动重试",
                    )
                    attention.append(job["id"])
                    continue
                if state == "error":
                    _fail_job(job,
                              "ComfyUI 任务在服务重启前已执行失败，请重新生成",
                              "ComfyUI 任务重启前已失败，请重新生成")
                    interrupted.append(job["id"])
                    continue
                # lost：history 与队列都明确应答没有该任务
                _fail_job(job,
                          "生成在服务重启时中断：ComfyUI 历史与队列均未找到"
                          "该任务（可能已被清理），请重新生成",
                          "服务重启时生成中断，请重新生成")
                interrupted.append(job["id"])
                continue
            _fail_job(job,
                      "生成在服务重启时中断（不支持断点续算），请重新生成",
                      "服务重启时生成中断，请重新生成")
            interrupted.append(job["id"])

        # ---- needs_attention：上次断线待核查的任务，再探一次 ----
        for job in self._iter_jobs("needs_attention"):
            shot_id = job.get("shot_id") or ""
            prompt_id = job.get("prompt_id") or ""
            if is_comfy_engine(job.get("engine")) and prompt_id:
                state = self._probe_comfy_job(prompt_id, self._comfy_url(job))
                if state in ("done", "running", "queued"):
                    db_module.db.update_job(
                        job["id"], status="running",
                        stage="恢复等待（服务重启）", progress=0.0, error="",
                    )
                    self._queue.put_nowait(job["id"])
                    if shot_id and sb.get_shot(shot_id):
                        sb.set_shot_status(shot_id, "generating",
                                           review_stage="generation")
                    recovered.append(job["id"])
                    continue
                if state == "temporarily_unavailable":
                    continue  # 仍不可达：继续待核查，不动
                if state == "error":
                    _fail_job(job,
                              "ComfyUI 任务在服务重启前已执行失败，请重新生成",
                              "ComfyUI 任务重启前已失败，请重新生成")
                    interrupted.append(job["id"])
                    continue
                # lost：确认没有了
                _fail_job(job,
                          "生成在服务重启时中断：ComfyUI 历史与队列均未找到"
                          "该任务（可能已被清理），请重新生成",
                          "服务重启时生成中断，请重新生成")
                interrupted.append(job["id"])
                continue
            # 没有 prompt_id 的待核查任务（理论上不出现）：无法 resume，
            # 明确标中断让用户重新生成
            _fail_job(job,
                      "生成在服务重启时中断（不支持断点续算），请重新生成",
                      "服务重启时生成中断，请重新生成")
            interrupted.append(job["id"])

        if recovered:
            print(f"[queue] 恢复了 {len(recovered)} 个 ComfyUI 任务（重启后按 prompt_id 继续等待）")
        if attention:
            print(f"[queue] {len(attention)} 个 ComfyUI 任务进入待核查（服务暂不可达，未误标失败）")
        if interrupted:
            print(f"[queue] 标记了 {len(interrupted)} 个中断任务（服务重启前正在生成）")

        # ---- pending：重新入队（保持提交顺序，分页拉取） ----
        pending_count = 0
        for job in self._iter_jobs("pending", order="asc"):
            self._queue.put_nowait(job["id"])
            pending_count += 1
            shot_id = job.get("shot_id") or ""
            if shot_id and sb.get_shot(shot_id):
                sb.set_shot_status(shot_id, "queued")
        if pending_count:
            print(f"[queue] 重新入队 {pending_count} 个未执行任务")

    @staticmethod
    def _probe_comfy_job(prompt_id: str, base_url: str = "") -> str:
        """重启恢复探测：ComfyUI 侧任务状态。

        done/running/queued/error/lost/temporarily_unavailable。
        探测客户端本身异常（构造失败等）按 temporarily_unavailable 处理
        （HF-04：连不上不等于任务不存在，保守起见不标失败）。
        """
        try:
            from .comfyui import ComfyUIClient

            return ComfyUIClient(base_url or None).probe(prompt_id)
        except Exception:
            return "temporarily_unavailable"

    @staticmethod
    def _comfy_url(job: dict) -> str:
        from .comfyui import ComfyUIClient
        url = job.get("comfy_url") or ""
        if not url:
            # 旧任务没有地址可追溯，只在首次恢复时取当前设置并落库。
            url = ComfyUIClient().base_url
            db_module.db.update_job(job["id"], comfy_url=url)
            job["comfy_url"] = url
        return url

    def stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._executor.shutdown(wait=False)

    # ---------- 外部操作 ----------
    async def submit(self, params: dict) -> str:
        from .generation import prepare_comfy_workflow, persist_prepared_job
        graph, template = None, None
        # ComfyUI 通道（默认引擎）入队即固化工作流快照；兜底通道不建快照
        if is_comfy_engine(params["engine"]):
            graph, template = prepare_comfy_workflow(params, output_dir=OUTPUTS_DIR)
        job = persist_prepared_job(db_module.db, params, OUTPUTS_DIR, graph=graph, template=template)
        await self._queue.put(job["id"])
        return job["id"]

    async def enqueue(self, job_id: str) -> None:
        """把已创建的 job 入队。

        router 需要「先建 job → 落 ComfyUI 工作流快照 → 再入队」时用
        （2B：入队即固化快照，排队期间用户改模板不影响已排队任务）。
        """
        await self._queue.put(job_id)

    def cancel(self, job_id: str):
        job = db_module.db.get_job(job_id)
        # HF-04：needs_attention（断线待核查）也允许取消——用户放弃等待
        if not job or job["status"] not in ("pending", "running", "needs_attention"):
            return
        self._cancelled.add(job_id)
        if job["status"] in ("pending", "needs_attention"):
            # 还没开始跑（或待核查未入队）：直接标记取消，worker 取到后会跳过
            db_module.db.cancel_job(job_id, statuses=("pending", "needs_attention"))

    # ---------- worker ----------
    async def _worker(self):
        while True:
            job_id = await self._queue.get()
            try:
                if job_id in self._cancelled:
                    # 排队阶段已被取消
                    db_module.db.cancel_job(job_id)
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
        if not job or job["status"] not in ("pending", "running"):
            return

        # 首次 await 前认领任务，避免释放旧模型时取消 pending 又被改回 running。
        if not db_module.db.claim_job(job_id):
            return

        # 分镜工作台联动：该 job 若由分镜发起，全程同步镜头状态
        shot_id = job.get("shot_id") or ""

        # 引擎实例缓存（模型常驻内存，切任务不重载）
        engine_id = job["engine"]
        # ComfyUI 通道按 (引擎, 服务地址) 缓存：同一引擎可能连不同服务器；
        # 兜底通道（进程内推理）没有远端地址，用引擎 id 作键
        engine_key = (engine_id, self._comfy_url(job)) if is_comfy_engine(engine_id) else engine_id
        try:
            for cached_id, cached in list(self._engine_instances.items()):
                if cached_id != engine_key and hasattr(cached, "unload"):
                    await asyncio.get_running_loop().run_in_executor(self._executor, cached.unload)
                    del self._engine_instances[cached_id]
            if engine_key not in self._engine_instances:
                engine = get_engine(engine_id)()
                # 只有 ComfyUI 通道需要按任务保存的地址绑定服务（engine_key 是
                # (id, url) 元组）；兜底通道（anime / wan22 / h3）没有远端地址，
                # 早先这里对所有带 bind_server 的引擎取 engine_key[1]，对字符串
                # "h3" 会取到 "3"——判断类型后再绑，避免把引擎绑到错误地址。
                if isinstance(engine_key, tuple) and hasattr(engine, "bind_server"):
                    engine.bind_server(engine_key[1])
                self._engine_instances[engine_key] = engine
            engine = self._engine_instances[engine_key]
        except Exception as e:
            db_module.db.update_job(
                job_id, status="failed", error=f"引擎加载失败: {e}",
                stage="失败", finished_at=_utcnow(),
            )
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note=f"引擎加载失败: {e}"[:200])
                sb.set_shot_status(shot_id, "rework", review_stage="")
            return

        started = time.time()

        if shot_id and sb.get_shot(shot_id):
            sb.set_shot_status(shot_id, "generating")

        frames_dir = OUTPUTS_DIR / job_id / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        def on_progress(p: float, stage: str):
            db_module.db.update_job(
                job_id, progress=round(min(max(p, 0.0), 1.0), 4), stage=stage
            )

        def on_submitted(prompt_id: str, client_id: str):
            # P0-01：ComfyUI /prompt 返回即持久化（在 worker 线程调用，
            # db 内部有锁，线程安全）——不等 generate 返回。崩溃恢复与
            # 对账都靠这两列；client_id 同时落库供恢复时续 WS 进度。
            if db_module.db.get_job(job_id):
                db_module.db.update_job(
                    job_id, prompt_id=prompt_id, client_id=client_id,
                    stage="ComfyUI 已受理，生成中",
                )

        def is_cancelled() -> bool:
            return job_id in self._cancelled

        loop = asyncio.get_running_loop()
        # 2B：ComfyUI 恢复执行——job 带上 prompt_id 说明是重启恢复的任务，
        # 跳过提交直接按 prompt_id 继续等（正常流程执行前 prompt_id 必然为空）
        resume_prompt_id = ""
        resume_client_id = ""
        workflow_snapshot = ""
        if is_comfy_engine(job.get("engine")):
            resume_prompt_id = job.get("prompt_id") or ""
            # P0-01：提交时持久化的 client_id——恢复后续 WS 进度；
            # 老任务没有该列，为空走轮询（兼容旧行为）
            resume_client_id = job.get("client_id") or ""
            workflow_snapshot = job.get("workflow_snapshot") or ""
        try:
            if is_cancelled():
                raise GenerationCancelled()
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
                    first_frame_image=job.get("first_frame_image") or "",
                    last_frame_image=job.get("last_frame_image") or "",
                    resume_prompt_id=resume_prompt_id,
                    workflow_snapshot=workflow_snapshot,
                    on_submitted=on_submitted,
                    resume_client_id=resume_client_id,
                ),
            )
            if is_cancelled():
                raise GenerationCancelled()
        except GenerationCancelled:
            note = ("已取消（ComfyUI 任务可能仍在远端执行）"
                    if is_comfy_engine(engine_id) else "已取消")
            db_module.db.cancel_job(job_id, note=note)
            self._cancelled.discard(job_id)
            return
        except Exception as e:
            db_module.db.update_job(
                job_id, status="failed", error=str(e), stage="失败", finished_at=_utcnow()
            )
            if shot_id and sb.get_shot(shot_id):
                sb.update_shot(shot_id, job_id="", note=str(e)[:200])
                sb.set_shot_status(shot_id, "rework", review_stage="")
            return

        # 2B：ComfyUI prompt_id 持久化——重启恢复与对账都靠它。
        # P0-01 起主路径是 on_submitted（提交即落库）；这里是兜底：
        # 引擎未触发回调（如恢复路径）或回调写入失败时补写。
        prompt_id = getattr(engine, "last_prompt_id", "") or ""
        if prompt_id and db_module.db.get_job(job_id):
            db_module.db.update_job(job_id, prompt_id=prompt_id)

        # 2C：ComfyUI 原生视频原样归档——remux（-c copy 码流原样 + 元数据），
        # 免抽帧重编码；没有原生视频才走帧序列合成（本地引擎/图片序列输出）
        native_video = getattr(engine, "last_native_video", "") or ""
        native_output = getattr(engine, "last_output_file", None)
        if native_output:
            db_module.db.update_job(job_id, native_output=json.dumps(native_output))
        final_path = OUTPUTS_DIR / f"{job_id}.mp4"
        metadata = self._job_metadata(job, engine)
        db_module.db.update_job(job_id, hardware=metadata["hardware"])
        if native_video and Path(native_video).is_file():
            native_path = Path(native_video)
            db_module.db.update_job(job_id, stage="归档原片", progress=0.92)
            try:
                await loop.run_in_executor(
                    self._executor,
                    lambda: remux_video(native_path, final_path, metadata=metadata),
                )
            except Exception:
                # remux 失败兜底：原样拷贝（丢元数据不丢成片）
                try:
                    await loop.run_in_executor(
                        self._executor,
                        lambda: shutil.copyfile(native_path, final_path),
                    )
                except Exception as e:
                    db_module.db.update_job(
                        job_id, status="failed", error=f"视频归档失败: {e}",
                        stage="失败", finished_at=_utcnow(),
                    )
                    if shot_id and sb.get_shot(shot_id):
                        sb.update_shot(shot_id, job_id="", note=f"视频归档失败: {e}"[:200])
                        sb.set_shot_status(shot_id, "rework", review_stage="")
                    return
            # 清理下载临时文件（含 VHS_VideoCombine 可能产生的同名子目录）
            native_path.unlink(missing_ok=True)
            shutil.rmtree(native_path.parent / native_path.stem, ignore_errors=True)
            # 海报帧：从成片抽第一帧（画廊/审核要用；失败不阻断）
            try:
                await loop.run_in_executor(
                    self._executor,
                    lambda: extract_first_frame(final_path, frames_dir / "frame_0000.png"),
                )
            except Exception:
                pass
        else:
            # 合成 MP4（参数写入元数据，画廊可读回复用）
            db_module.db.update_job(job_id, stage="合成视频", progress=0.92)
            try:
                await loop.run_in_executor(
                    self._executor,
                    lambda: frames_to_video(
                        frames_dir,
                        final_path,
                        job["fps"],
                        metadata=metadata,
                    ),
                )
            except Exception as e:
                db_module.db.update_job(
                    job_id, status="failed", error=f"视频合成失败: {e}",
                    stage="失败", finished_at=_utcnow(),
                )
                if shot_id and sb.get_shot(shot_id):
                    sb.update_shot(shot_id, job_id="", note=f"视频合成失败: {e}"[:200])
                    sb.set_shot_status(shot_id, "rework", review_stage="")
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
        # 分镜工作台联动：落一个候选版本，镜头进入待审核（3A）
        if shot_id and sb.get_shot(shot_id):
            done_job = db_module.db.get_job(job_id)
            version = sb.create_version(shot_id, done_job, video_url, poster_url or "")
            fields = {"job_id": "", "note": ""}
            if sb.shot_references(shot_id)["timeline_items"]:
                # 已入时间线的镜头：只追加候选版本，不静默更换采用指针，
                # 避免已排期片段对应的成片在用户不知情时被替换；是否采用
                # 由用户在审核中显式决定（adopt_version / review_shot 均有
                # 同样的引用守卫，409 + 片段明细）
                pass
            else:
                fields["current_version_id"] = version["id"]
            sb.update_shot(shot_id, **fields)
            sb.set_shot_status(shot_id, "review", review_stage="video")
        self._cancelled.discard(job_id)

    @staticmethod
    def _job_metadata(job: dict, engine) -> dict:
        """嵌入 MP4 的生成参数（FreeVideo 式视频自带工作流）"""
        profile = getattr(engine, "_profile", None)
        hw = profile.to_dict() if profile is not None else hw_probe.probe().to_dict()
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
            "preview_kind": job.get("preview_kind") or "rerun",
            "parent_job_id": job.get("parent_job_id") or "",
            "generation_inputs": json.loads(job.get("generation_inputs") or "[]"),
            "characters": json.loads(job.get("characters_snapshot") or "[]"),
            "hardware": ("ComfyUI 外部执行，资源由服务端管理" if is_comfy_engine(job["engine"])
                         else f"{hw['gpu_name']} | {hw['quant']} | {hw['offload']}"),
        }


# 全局单例：API 路由与 FastAPI lifespan 共用同一个队列
job_queue = JobQueue()
