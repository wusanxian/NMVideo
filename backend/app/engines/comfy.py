"""ComfyUI 引擎：把镜头参数注入工作流模板，提交到 ComfyUI 服务器执行。

产物处理：
  - 视频类输出（VHS_VideoCombine 等）→ ffmpeg 抽帧到 frames_dir
  - 图片序列输出（SaveImage 等）   → 逐张下载到 frames_dir
统一返回帧路径列表，由队列管理器合成 MP4（与本地引擎走同一条链路）。

2B 任务可靠性：
  - 提交与 WS 订阅共用同一 client_id（否则收不到进度推送）
  - prompt_id 提交即持久化（P0-01）：/prompt 返回后立刻经 on_submitted
    回调交队列落库（prompt_id + client_id），不等 _wait 完成——ComfyUI
    已受理但本服务崩溃时，重启恢复靠库里的 prompt_id 对账，不重复提交
  - resume_prompt_id / resume_client_id：恢复执行——跳过提交，按已持久化
    的 prompt_id 继续等（client_id 在库则续 WS 进度，否则走历史轮询）
  - workflow_snapshot：入队时固化的工作流快照；排队期间用户改模板不影响
    已排队任务

2C 输入上传与产物原样归档：
  - 参考图（i2v/首尾帧）上传到 ComfyUI input 目录，用返回名注入工作流
  - ComfyUI 原生视频输出原样归档：下载后设置 last_native_video 并返回空帧
    列表，由队列 remux（-c copy 码流原样 + 元数据），不抽帧不重编码

HF-00 跨任务状态隔离：
  - 引擎实例被队列缓存复用，generate() 入口即清零 last_prompt_id /
    last_native_video——任务 A 的原生视频不会被只产图片帧的任务 B 继承
"""
import json
from pathlib import Path
from typing import Callable, List

from .base import BaseEngine, EngineMeta, GenerationCancelled

from ..comfyui import (
    ComfyUICancelled,
    ComfyUIClient,
    ComfyUIError,
    build_shot_workflow,
    extract_output_files,
    load_workflow_template,
    upload_reference_image,
)
from ..config import OUTPUTS_DIR


class ComfyEngine(BaseEngine):
    meta = EngineMeta(
        id="comfyui",
        name="ComfyUI 工作流",
        description="提交到本机/局域网 ComfyUI 服务器执行自定义工作流（进度实时推送）",
        status="ready",
        note="",
    )

    def __init__(self):
        self.client = ComfyUIClient()
        self._avail_cache = (0.0, False)  # (timestamp, available)
        # 2B：最近一次提交的 prompt_id（worker 负责持久化到 job 记录）
        self.last_prompt_id = ""
        # 2C：最近一次产出的原生视频路径（worker remux 原样归档；
        # 为空表示走帧序列合成）
        self.last_native_video = ""

    def is_available_cached(self, ttl: float = 10.0) -> bool:
        import time

        now = time.time()
        if now - self._avail_cache[0] < ttl:
            return self._avail_cache[1]
        ok = self.client.is_available()
        self._avail_cache = (now, ok)
        return ok

    def describe(self) -> dict:
        """动态状态：ComfyUI 服务器可达才算 ready"""
        ok = self.is_available_cached()
        return {
            "id": self.meta.id,
            "name": self.meta.name,
            "description": self.meta.description,
            "status": "ready" if ok else "unavailable",
            "note": "" if ok else f"未连接到 ComfyUI（{self.client.base_url}）",
        }

    def generate(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        num_frames: int,
        fps: int,
        steps: int,
        guidance: float,
        seed: int,
        frames_dir: Path,
        on_progress: Callable[[float, str], None],
        is_cancelled: Callable[[], bool],
        reference_image: str = "",
        resume_prompt_id: str = "",
        workflow_snapshot: str = "",
        on_submitted: Callable[[str, str], None] = None,
        resume_client_id: str = "",
    ) -> List[Path]:
        # HF-00：引擎实例被 queue_manager 跨任务缓存（模型常驻内存），
        # 上一轮的 last_* 必须在这里清零——否则任务 A 产出的原生视频会
        # 被任务 B（只产图片帧）继承，worker 按 last_native_video 优先
        # 归档，导致视频串片。失败/取消同样不带旧值进入下一轮。
        self.last_prompt_id = ""
        self.last_native_video = ""
        if not self.client.is_available():
            raise ComfyUIError(
                f"连不上 ComfyUI 服务器（{self.client.base_url}）："
                "请先启动 ComfyUI，或在分镜编辑弹窗里修改服务器地址"
            )

        inject = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "seed": seed,
            "steps": steps,
            "guidance": guidance,
            # 模板 _aivideo_map 映射了 reference_image 才注入；值为 ComfyUI
            # input 目录内的引用名（2C：入队时 router 已上传并写进快照，
            # 无快照的兜底路径在 _resolve_workflow 里现场上传）
            "reference_image": reference_image,
        }

        if resume_prompt_id:
            # ---- 恢复路径：服务重启后按已持久化的 prompt_id 继续等 ----
            prompt_id = resume_prompt_id
            on_progress(0.05, "恢复等待 ComfyUI 任务（服务重启恢复）")
            # 带上提交时持久化的 client_id：新连接订阅同一 id 可继续收 WS
            # 进度；为空（老任务/上次没落库）走历史轮询
            entry = self._wait(prompt_id, client_id=resume_client_id,
                               on_progress=on_progress, is_cancelled=is_cancelled)
        else:
            # ---- 正常路径：快照/模板 → 注入 → 提交（client_id 与 WS 一致） ----
            graph = self._resolve_workflow(workflow_snapshot, inject, frames_dir)
            on_progress(0.03, "提交工作流到 ComfyUI")
            client_id = self.client.new_client_id()
            prompt_id = self.client.submit(graph, client_id=client_id)
            # P0-01：提交即持久化——/prompt 返回后立刻把 prompt_id +
            # client_id 交队列落库，不等 _wait 完成。ComfyUI 已受理但
            # NMVideo 在等待期间崩溃时，重启恢复靠库里的 prompt_id 对账
            # （/history + /queue 探测），不会重复提交同一任务。
            self.last_prompt_id = prompt_id
            if on_submitted:
                try:
                    on_submitted(prompt_id, client_id)
                except Exception:
                    # 持久化失败不阻断生成；该任务重启时按 lost 处理（标失败）
                    pass
            entry = self._wait(prompt_id, client_id=client_id, on_progress=on_progress,
                               is_cancelled=is_cancelled)

        self.last_prompt_id = prompt_id

        files = extract_output_files(entry)
        if not files:
            raise ComfyUIError(
                "ComfyUI 工作流没有产出文件：请确认模板最后是 SaveImage / "
                "VHS_VideoCombine 等输出节点，并检查 ComfyUI 端日志"
            )

        # 优先取视频；只有图片序列时逐张下载
        videos = [f for f in files if f["kind"] in ("videos", "gifs")]
        frames_dir.mkdir(parents=True, exist_ok=True)

        if videos:
            # 2C：原生视频原样归档——下载后交队列 remux（-c copy 码流原样 +
            # 元数据），不抽帧不重编码；返回空帧列表即通知队列走直通分支
            v = videos[0]
            src = OUTPUTS_DIR / f"comfy_{prompt_id}{Path(v['filename']).suffix or '.mp4'}"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(
                self.client.download(v["filename"], v["subfolder"], v["type"])
            )
            self.last_native_video = str(src)
            on_progress(0.99, "ComfyUI 原生视频就绪")
            return []
        else:
            on_progress(0.96, "下载 ComfyUI 图片序列")
            images = sorted(files, key=lambda f: f["filename"])
            for i, f in enumerate(images):
                if is_cancelled and is_cancelled():
                    raise GenerationCancelled()
                data = self.client.download(f["filename"], f["subfolder"], f["type"])
                (frames_dir / f"frame_{i:04d}.png").write_bytes(data)
                on_progress(0.96 + 0.03 * (i + 1) / len(images), f"下载帧 {i + 1}/{len(images)}")

        frames = sorted(frames_dir.glob("frame_*.png"))
        if not frames:
            raise ComfyUIError("帧目录为空：ComfyUI 产物解析失败")
        on_progress(0.99, "ComfyUI 产物就绪")
        return frames

    # ---------- 2B 内部 helpers ----------
    def _wait(self, prompt_id: str, client_id: str, on_progress, is_cancelled) -> dict:
        """等待完成；把客户端级取消异常转换为引擎级 GenerationCancelled
        （取消的 job 应标 cancelled、镜头回 draft，而不是走失败路径）。"""
        try:
            return self.client.wait_for_completion(
                prompt_id, on_progress=on_progress, is_cancelled=is_cancelled,
                client_id=client_id,
            )
        except ComfyUICancelled:
            raise GenerationCancelled()

    def _resolve_workflow(self, snapshot_path: str, inject: dict, frames_dir: Path) -> dict:
        """确定本次执行用的工作流图（2B：排队后不读可变模板）。

        优先用入队时固化的快照；没有快照文件（老任务/快照被清理）才现场
        读模板，并立即补一份快照，保证每个 ComfyUI 任务都有独立工作流存档。

        HF-05：快照**存在但损坏/不可读/无有效节点图**时明确抛
        ComfyUIError 需人工处理——老任务重试悄悄改用当前模板会导致
        输出不可复现（同一镜头两次生成用了不同工作流）。只有快照文件
        不存在才允许走模板兜底。

        2C：兜底路径下参考图在此上传——工作流确实映射了 reference_image
        才传（默认模板不映射，纯文生视频不受影响）；上传失败抛
        ComfyUIError 让任务明确失败，而不是提交一个必然报错的工作流。
        """
        job_id = frames_dir.parent.name  # frames_dir = outputs/{job_id}/frames
        if snapshot_path:
            p = Path(snapshot_path)
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    graph = data.get("graph") or data.get("prompt")
                except (ValueError, OSError) as e:
                    raise ComfyUIError(
                        f"工作流快照损坏或不可读: {p}（{e}）。为保证输出可复现，"
                        "请人工处理后重试；系统不会静默改用当前模板"
                    )
                if not isinstance(graph, dict) or not graph:
                    raise ComfyUIError(
                        f"工作流快照缺少有效节点图: {p}。请人工处理后重试；"
                        "系统不会静默改用当前模板"
                    )
                return graph
            # 快照文件不存在（老任务/被清理）→ 走下面的模板兜底并补写
        template = load_workflow_template()
        inject = dict(inject)
        if inject.get("reference_image") and (template.get("_aivideo_map") or {}).get(
            "reference_image"
        ):
            inject["reference_image"] = upload_reference_image(
                self.client, inject["reference_image"]
            )
        graph = build_shot_workflow(inject, template=template)
        snap = OUTPUTS_DIR / "workflows" / f"{job_id}.json"
        try:
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(
                json.dumps({"job_id": job_id, "graph": graph}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # 快照写失败不阻断生成
        return graph
