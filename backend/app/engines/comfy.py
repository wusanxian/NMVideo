"""ComfyUI 引擎：把镜头参数注入工作流模板，提交到 ComfyUI 服务器执行。

产物处理：
  - 视频类输出（VHS_VideoCombine 等）→ ffmpeg 抽帧到 frames_dir
  - 图片序列输出（SaveImage 等）   → 逐张下载到 frames_dir
统一返回帧路径列表，由队列管理器合成 MP4（与本地引擎走同一条链路）。
"""
import json
import shutil
from pathlib import Path
from typing import Callable, List

from .base import BaseEngine, EngineMeta, GenerationCancelled

from ..comfyui import (
    ComfyUIClient,
    ComfyUIError,
    build_workflow,
    extract_output_files,
    load_workflow_template,
)
from ..config import OUTPUTS_DIR
from ..video import get_ffmpeg


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
    ) -> List[Path]:
        if not self.client.is_available():
            raise ComfyUIError(
                f"连不上 ComfyUI 服务器（{self.client.base_url}）："
                "请先启动 ComfyUI，或在分镜编辑弹窗里修改服务器地址"
            )

        template = load_workflow_template()
        mapping = template.get("_aivideo_map", {})
        inject = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "seed": seed,
            "steps": steps,
            "guidance": guidance,
        }
        # 参考图注入：模板 _aivideo_map 里配置了 "reference_image" → 节点/输入名时才注入
        # （LoadImage 的 image 字段填 /outputs/... 相对 ComfyUI input 目录的路径）
        if reference_image and "reference_image" in (mapping or {}):
            inject["reference_image"] = reference_image
        graph = build_workflow(template, mapping, inject)

        on_progress(0.03, "提交工作流到 ComfyUI")
        prompt_id = self.client.submit(graph)

        def _cancelled() -> bool:
            return bool(is_cancelled and is_cancelled())

        entry = self.client.wait_for_completion(
            prompt_id, on_progress=on_progress, is_cancelled=_cancelled
        )

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
            on_progress(0.96, "下载 ComfyUI 视频并抽帧")
            src = OUTPUTS_DIR / f"comfy_{prompt_id}.mp4"
            src.write_bytes(
                self.client.download(videos[0]["filename"], videos[0]["subfolder"], videos[0]["type"])
            )
            self._video_to_frames(src, frames_dir, fps)
            shutil.rmtree(src.parent / f"comfy_{prompt_id}", ignore_errors=True)
            src.unlink(missing_ok=True)
        else:
            on_progress(0.96, "下载 ComfyUI 图片序列")
            images = sorted(files, key=lambda f: f["filename"])
            for i, f in enumerate(images):
                if _cancelled():
                    raise GenerationCancelled()
                data = self.client.download(f["filename"], f["subfolder"], f["type"])
                (frames_dir / f"frame_{i:04d}.png").write_bytes(data)
                on_progress(0.96 + 0.03 * (i + 1) / len(images), f"下载帧 {i + 1}/{len(images)}")

        frames = sorted(frames_dir.glob("frame_*.png"))
        if not frames:
            raise ComfyUIError("帧目录为空：ComfyUI 产物解析失败")
        on_progress(0.99, "ComfyUI 产物就绪")
        return frames

    @staticmethod
    def _video_to_frames(video: Path, frames_dir: Path, fps: int) -> None:
        ffmpeg = get_ffmpeg()
        import subprocess

        proc = subprocess.run(
            [
                ffmpeg, "-y", "-i", str(video),
                "-vf", f"fps={fps}",
                "-start_number", "0",
                str(frames_dir / "frame_%04d.png"),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not list(frames_dir.glob("frame_*.png")):
            raise ComfyUIError(f"视频抽帧失败: {proc.stderr[-500:]}")
