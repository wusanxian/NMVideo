"""引擎抽象基类：所有视频生成引擎实现同一接口，可插拔"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# 执行通道：项目以 ComfyUI 为唯一视频生成底座（2026-10-22 架构口径）。
# 引擎只描述「用哪张工作流图 + 支持哪些输入槽位」，不在 NMVideo 进程内加载
# 视频模型；进程内推理（anime/wan/h3）属于历史通道，仅作 ComfyUI 不可用时的
# 兜底，不再作为默认引擎，也不承诺与 ComfyUI 路径同参数同结果。
EXECUTION_COMFY = "comfyui"
EXECUTION_NATIVE = "native"


@dataclass
class EngineMeta:
    id: str
    name: str
    description: str
    status: str = "ready"  # ready / unavailable
    note: str = ""
    supported_modes: tuple = ("t2v",)
    # 执行通道：comfyui（默认）/ native（兜底）
    execution_channel: str = EXECUTION_NATIVE
    # 走 ComfyUI 时按模式声明的内置预设 id（mode -> preset_id）。
    # 为空表示使用用户在「ComfyUI 设置」里 apply 的当前模板。
    workflow_presets: dict = field(default_factory=dict)


class EngineUnavailable(Exception):
    """引擎不可用（缺模型 / 硬件不足）"""


class GenerationCancelled(Exception):
    """用户取消生成（由 is_cancelled 检查点抛出）"""


ProgressCallback = Callable[[float, str], None]  # (进度 0~1, 阶段描述)
CancelCheck = Callable[[], bool]
SubmittedCallback = Callable[[str, str], None]  # (prompt_id, client_id) 提交即回调


class BaseEngine(ABC):
    meta: EngineMeta

    @abstractmethod
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
        on_progress: ProgressCallback,
        is_cancelled: CancelCheck,
        reference_image: str = "",
        first_frame_image: str = "",
        last_frame_image: str = "",
        resume_prompt_id: str = "",
        workflow_snapshot: str = "",
        on_submitted: Optional[SubmittedCallback] = None,
        resume_client_id: str = "",
    ) -> List[Path]:
        """生成帧序列，逐帧保存到 frames_dir，返回帧文件路径列表。

        reference_image: 参考图/首帧 URL（图生视频/首尾帧/续接模式）；
        不支持的引擎可忽略该参数（保持纯文生视频行为）。
        first_frame_image / last_frame_image: 独立首帧/尾帧 URL（PR-02，
        first_last 模式分别注入工作流首/尾帧节点；其他引擎忽略）。
        resume_prompt_id: ComfyUI 专用——服务重启后恢复执行：跳过提交，
        直接按已持久化的 prompt_id 等待/下载（其他引擎忽略）。
        workflow_snapshot: ComfyUI 专用——入队时固化的工作流快照路径，
        避免排队期间用户改模板影响已排队任务（其他引擎忽略）。
        on_submitted: ComfyUI 专用（P0-01）——HTTP /prompt 返回后**立即**
        回调 (prompt_id, client_id)，由队列落库；不等生成完成。ComfyUI
        已受理但 NMVideo 崩溃时，重启恢复靠库里的 prompt_id 对账，不会
        重复提交。本地引擎忽略。
        resume_client_id: ComfyUI 专用——恢复执行时带上提交时持久化的
        client_id（新连接订阅同一 id 可继续收 WS 进度；为空走轮询）。

        返回契约（2C）：通常返回帧路径列表，由队列合成 MP4；若引擎产出
        的是原生视频（ComfyUI VHS_VideoCombine 等），把视频路径写到
        self.last_native_video 并返回空列表——队列会 remux 原样归档
        （免抽帧重编码），本地引擎恒返回帧列表。
        """
        raise NotImplementedError
