"""引擎抽象基类：所有视频生成引擎实现同一接口，可插拔"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List


@dataclass
class EngineMeta:
    id: str
    name: str
    description: str
    status: str = "ready"  # ready / unavailable
    note: str = ""


class EngineUnavailable(Exception):
    """引擎不可用（缺模型 / 硬件不足）"""


class GenerationCancelled(Exception):
    """用户取消生成（由 is_cancelled 检查点抛出）"""


ProgressCallback = Callable[[float, str], None]  # (进度 0~1, 阶段描述)
CancelCheck = Callable[[], bool]


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
        resume_prompt_id: str = "",
        workflow_snapshot: str = "",
    ) -> List[Path]:
        """生成帧序列，逐帧保存到 frames_dir，返回帧文件路径列表。

        reference_image: 参考图/首帧 URL（图生视频/首尾帧/续接模式）；
        不支持的引擎可忽略该参数（保持纯文生视频行为）。
        resume_prompt_id: ComfyUI 专用——服务重启后恢复执行：跳过提交，
        直接按已持久化的 prompt_id 等待/下载（其他引擎忽略）。
        workflow_snapshot: ComfyUI 专用——入队时固化的工作流快照路径，
        避免排队期间用户改模板影响已排队任务（其他引擎忽略）。
        """
        raise NotImplementedError
