"""FreeVideo H3 引擎：MiniMax VDN-H3 本地推理（FreeVideo 官方 ComfyUI 插件）。

模型链路：MiniMax H3 底模 → OpenVDN VDN-H3（Video DeltaNet 混合注意力，
8 步采样）。本引擎不自带推理实现，而是把 FreeVideo 的官方 ComfyUI 插件
作为执行后端——与 FlashML-org/FreeVideo 用同一套模型与节点协议。

与普通 ComfyUI 引擎的区别（用户可自主切换的意义）：
  - 模板按引擎自动路由（comfyui.resolve_template_for）：选中 h3 引擎即
    固定使用 freevideo_h3_{t2v,i2v,first_last} 内置预设，与「当前模板」
    解耦，无需去设置页手动 apply；
  - 参数契约按 FreeVideo 官方协议归一（freevideo.normalize_request）：
    宽高 32 对齐、帧数 5+17n、固定 8 步、24fps、原生预览两遍采样；
  - 角色参考图 / LoRA 注入官方 Reference / LoRA 节点（freevideo.attach_characters）。

前置条件：ComfyUI + custom_nodes 安装 FlashML-org/FreeVideo。
硬件底线（官方声明）：8GB 显存 + 16GB 内存；GeForce/RTX 30 系自动 int8，
工作站/数据中心卡 FP8。
"""
from pathlib import Path
from typing import Callable, List

from .base import BaseEngine, EngineMeta
from .comfy import ComfyEngine


class H3Engine(BaseEngine):
    meta = EngineMeta(
        id="h3",
        name="FreeVideo H3（MiniMax VDN-H3）",
        description="MiniMax H3 本地推理 · 8 步出片 · 8GB 显存起步 · 需 ComfyUI + FreeVideo 插件",
        status="ready",
        note="",
        supported_modes=("t2v", "i2v", "first_last", "continuation"),
    )

    def __init__(self):
        # 执行通道完全复用 ComfyEngine：快照固化、prompt_id 持久化、
        # 断线恢复、原生视频归档、上传与产物处理全部同一套。
        self._comfy = ComfyEngine()
        self._node_cache = (0.0, True, "")  # (timestamp, node_ok, note)

    def bind_server(self, base_url: str) -> None:
        self._comfy.bind_server(base_url)

    def is_available_cached(self, ttl: float = 10.0) -> bool:
        return self._comfy.is_available_cached(ttl)

    def _probe_node(self, ttl: float = 10.0) -> tuple:
        """探测 FreeVideoGenerate 节点是否可用（describe 用，轻量缓存）。"""
        import time

        now = time.time()
        if now - self._node_cache[0] < ttl:
            return self._node_cache[1], self._node_cache[2]
        try:
            info = self._comfy.client.object_info(node_names=["FreeVideoGenerate"]) or {}
            ok = "FreeVideoGenerate" in info
            note = "" if ok else (
                "ComfyUI 已连接，但未安装 FreeVideo 插件——请把 "
                "FlashML-org/FreeVideo 克隆到 ComfyUI/custom_nodes 并重启 ComfyUI")
        except Exception as exc:  # 探测失败不炸 describe，给出可读说明
            ok, note = False, f"无法探测 FreeVideo 节点：{exc}"
        self._node_cache = (now, ok, note)
        return ok, note

    def describe(self) -> dict:
        base = self._comfy.describe()
        if base.get("status") != "ready":
            return {
                "id": self.meta.id,
                "name": self.meta.name,
                "description": self.meta.description,
                "status": "unavailable",
                "note": (f"未连接到 ComfyUI（{self._comfy.client.base_url}）"
                         "——H3 引擎经 ComfyUI 运行 FreeVideo 插件"),
            }
        ok, note = self._probe_node()
        return {
            "id": self.meta.id,
            "name": self.meta.name,
            "description": self.meta.description,
            "status": "ready" if ok else "unavailable",
            "note": note,
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
        first_frame_image: str = "",
        last_frame_image: str = "",
        resume_prompt_id: str = "",
        workflow_snapshot: str = "",
        on_submitted: Callable[[str, str], None] = None,
        resume_client_id: str = "",
    ) -> List[Path]:
        # 备料阶段（prepare_comfy_workflow）已按 h3 预设固化工作流快照，
        # 执行期与普通 ComfyUI 任务无异——直接走同一执行通道。
        return self._comfy.generate(
            prompt=prompt, negative_prompt=negative_prompt,
            width=width, height=height, num_frames=num_frames, fps=fps,
            steps=steps, guidance=guidance, seed=seed, frames_dir=frames_dir,
            on_progress=on_progress, is_cancelled=is_cancelled,
            reference_image=reference_image,
            first_frame_image=first_frame_image,
            last_frame_image=last_frame_image,
            resume_prompt_id=resume_prompt_id,
            workflow_snapshot=workflow_snapshot,
            on_submitted=on_submitted,
            resume_client_id=resume_client_id,
        )
