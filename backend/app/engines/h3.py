"""MiniMax H3 原生推理引擎（diffusers ModularPipeline）——**兜底通道**

架构口径：ComfyUI 是唯一视频生成底座，H3 的正路是 ``engines/h3_comfy.py``
（id = ``h3``，走 ComfyUI 工作流 + ``nmvideo_h3_*`` 预设）。本模块退为兜底：

- 引擎 id 为 ``h3_native``，**不是默认引擎**，只在 ComfyUI 不可用时手动选用；
- 与 ComfyUI 路径**不承诺同参数同结果**（工作流图、采样器、蒸馏档位由 ComfyUI
  侧决定），保留它是为了历史结果可复现 + 无 ComfyUI 环境仍能出片；
- 8GB 量化档（int4/int8 分级）仍在这里，是「没有 ComfyUI 但想跑 H3」的通道。

实现本身仍不依赖 ComfyUI、也不依赖 H3 插件节点：模型以 diffusers modular
组件形式发布在 OpenVDN/vdn-minimax-h3，diffusers 0.36+ 原生包含
MiniMaxH3ModularPipeline，用 ``ModularPipeline.from_pretrained(...)`` 进程内推理。

模型链路（8 步蒸馏版，官方 headline 配置）：::

    MiniMax H3 底模（h3-base: transformer / video+audio VAE / scheduler）
      + VDN-H3 8 步蒸馏（stage-dmd-step-250: linear_branch 混合注意力 + LoRA adapters）
      + Qwen3-VL-32B 文本编码器（MiniMaxAI/MiniMax-H3/text_encoder）

workflow（由 diffusers 的 blocks 名称决定加载哪些权重分区）：
  - ``t2va``  纯文生视频（+ 音频轨），只加载 ``transformer/`` 分区
  - ``fl2va`` 首帧 / 尾帧 / 首尾帧，同一套权重，额外吃 ``image`` / ``last_image``
  - ``ref2va`` 多参考图，需要另 61.7GB 的 ``transformer_ref/`` 分区——本引擎不启用

模型契约（来自 diffusers 0.41 的 MiniMaxH3Blocks 与官方 README）：
  - 宽高必须 32 的倍数；帧数吸附到 ``17n + 5``；固定 24fps；时长 5~15 秒
  - guidance 已蒸馏进权重：**没有 negative_prompt、没有 guidance_scale**
  - ``num_inference_steps`` 是 sigma 网格点数，9 个点 = 8 次模型前向

硬件：官方 diffusers 路径 345 帧在 24GB 卡上峰值约 22GB（fp8 约 20GB）；
显存不足时用 ``block_level`` 分组卸载，帧数越少峰值越低。**不支持 CPU**——
H3 是 61.7GB 的 3D transformer，CPU 上不是"慢"而是跑不动。
"""
import math
import os
import threading
from pathlib import Path
from typing import List

from .base import BaseEngine, EngineMeta, EngineUnavailable, GenerationCancelled
from ..config import HF_CACHE_DIR
from .. import hw_probe
from . import h3_quant

# 仓库与本地缓存（config 已把 HF_HOME 指到 models/hf-cache，不占 C 盘用户目录）
MODEL_REPO = "OpenVDN/vdn-minimax-h3"
BASE_REPO = "MiniMaxAI/MiniMax-H3"
HF_CACHE = HF_CACHE_DIR

# 底模仓库里本引擎需要的组件（h3-base 已自带 transformer/vae/scheduler）
BASE_ALLOW = ["text_encoder/*", "tokenizer/*", "processor/*", "*.json", "*.md"]

# 体积（GB，用于下载提示；实测来自 HF 文件清单）
SIZE_VDN = 78.0        # h3-base 72 + stage-b 4.3 + stage-dmd 5.1
SIZE_BASE = 62.1       # 只有 text_encoder 62.14 + tokenizer/processor 很小
SIZE_TOTAL = SIZE_VDN + SIZE_BASE

# 模型原生帧率与帧数区间（17n+5 且时长 5~15 秒 → 124 ~ 345 帧）
H3_FPS = 24
MIN_FRAMES = 124       # 17*7+5 = 124 → 5.17s
MAX_FRAMES = 345       # 17*20+5 = 345 → 14.375s

# 9 个 sigma 点 = 8 次前向（VDN-H3 的蒸馏档位固定）
H3_STEPS = 9


class H3Engine(BaseEngine):
    meta = EngineMeta(
        id="h3_native",
        name="MiniMax H3（原生兜底）",
        description=(
            "VDN-H3 8 步蒸馏在 NMVideo 进程内直连推理（含 8GB 量化档）· 不需要 "
            "ComfyUI H3 插件节点 · 需 NVIDIA 显卡（int8 约需 80GB 内存；"
            f"int4 实验档约需 52GB 内存）· 模型约 {SIZE_TOTAL:.0f}GB"
        ),
        status="ready",
        supported_modes=("t2v", "i2v", "first_last", "continuation"),
        # 兜底通道：进程内推理，仅 ComfyUI 不可用时使用（非默认引擎）。
        # H3 的正路是 engines/h3_comfy.py（id = h3，走 ComfyUI 预设）。
        execution_channel="native",
    )

    _pipe = None
    _pipe_workflow = ""
    _load_lock = threading.Lock()

    # ---------- 生命周期 ----------
    def unload(self):
        """释放管线与显存（queue_manager 切换引擎时调用）。"""
        with self._load_lock:
            self._pipe = None
            self._pipe_workflow = ""
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # ---------- 状态探测 ----------
    @staticmethod
    def model_ready() -> tuple:
        """本地缓存里两个仓库是否都已下载完整（只读、不联网）。

        返回 (ok, note)。用 snapshot_download 的 local_files_only 判定——
        这是唯一可靠的"文件齐不齐"判断（比手写文件清单更抗仓库变更）。
        """
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            return False, "缺少 huggingface_hub，无法确认模型是否已下载"
        missing = []
        for repo, allow in ((MODEL_REPO, None), (BASE_REPO, BASE_ALLOW)):
            try:
                snapshot_download(
                    repo, cache_dir=str(HF_CACHE), allow_patterns=allow,
                    local_files_only=True,
                )
            except Exception:
                missing.append(repo)
        if missing:
            return False, "、".join(missing)
        return True, ""

    def describe(self) -> dict:
        """如实报告三态：无显卡 / 显存不足 / 模型未下载 / 就绪。"""
        profile = hw_probe.probe()
        base = {
            "id": self.meta.id,
            "name": self.meta.name,
            "description": self.meta.description,
        }
        if not profile.torch_cuda_available:
            return {
                **base, "status": "unavailable",
                "note": (
                    "H3 需要 NVIDIA 显卡：本机 torch 未检测到 CUDA（MiniMax H3 是 61.7GB "
                    "的 3D transformer，CPU 上跑不动，不是慢的问题）。想在本机出片可用 "
                    "AnimeDiff 引擎（CPU 可跑）或等有 N 卡的机器。"
                ),
                "hardware": profile.to_dict(),
            }
        strategy = self._strategy(profile)
        plan = strategy["plan"]
        if not plan.available:
            return {
                **base, "status": "unavailable",
                "note": plan.reason,
                "plan": plan.to_dict(),
                "hardware": profile.to_dict(),
            }
        ok, missing = self.model_ready()
        if not ok:
            return {
                **base, "status": "unavailable",
                "note": (
                    f"模型未下载（缺 {missing}，共约 {SIZE_TOTAL:.0f}GB）。下载命令：\n"
                    f"  python scripts/download_models.py --h3\n"
                    f"或在「模型与设置」里点「下载 H3 模型」。"
                ),
                "plan": plan.to_dict(),
                "hardware": profile.to_dict(),
            }
        ram = profile.available_ram_gb if profile.available_ram_gb is not None else profile.ram_gb
        return {
            **base, "status": "ready",
            "note": (
                f"就绪（{profile.gpu_name} / {profile.vram_gb:.0f}GB 显存 / "
                f"{ram:.0f}GB 可用内存）。{strategy['note']}"
            ),
            "plan": plan.to_dict(),
            "hardware": profile.to_dict(),
        }

    @staticmethod
    def _strategy(profile) -> dict:
        """按「显存 × 内存」决定量化档位与权重放置。

        旧实现只看显存，且把 24GB 以上判成 ``offload=none``（权重直接
        ``.to("cuda")``）——transformer 是 61.7GiB，24GB 卡根本放不下，
        这条分支一旦真跑必然 OOM。现改为统一走 h3_quant.plan()：
        显存决定量化档位，内存决定能不能装下（加权卸载只省显存不省内存）。
        """
        ram = getattr(profile, "available_ram_gb", None)
        if ram is None:
            ram = getattr(profile, "ram_gb", 0.0)
        p = h3_quant.plan(
            getattr(profile, "vram_gb", 0.0),
            ram,
            getattr(profile, "compute_cap", ""),
        )
        return {
            "offload": p.offload,
            # fp8 键保留只为兼容旧调用点，量化实际走 quantization_config
            "fp8": False,
            "tier": p.tier,
            "quantization": dict(p.quantization),
            "max_pixels": p.max_pixels,
            "plan": p,
            "note": p.note if p.available else p.reason,
        }

    # ---------- 模型加载 ----------
    def _get_pipe(self, workflow: str, on_progress):
        """懒加载 + 单例缓存；workflow 变了要重载（组件分区不同）。"""
        with self._load_lock:
            if self._pipe is not None and self._pipe_workflow == workflow:
                return self._pipe
            if self._pipe is not None:  # 换 workflow：先释放旧的
                self._pipe = None
                self._pipe_workflow = ""
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass

            import torch
            from diffusers import ModularPipeline
            from diffusers.hooks import apply_group_offloading

            profile = hw_probe.probe()
            if not profile.torch_cuda_available:
                raise EngineUnavailable(
                    "H3 需要 NVIDIA 显卡（本机 torch 无 CUDA），无法在 CPU 上运行"
                )

            strategy = self._strategy(profile)
            plan = strategy["plan"]
            if not plan.available:
                raise EngineUnavailable(plan.reason)

            ok, missing = self.model_ready()
            if not ok:
                raise EngineUnavailable(
                    f"H3 模型未下载（缺 {missing}，约 {SIZE_TOTAL:.0f}GB）。"
                    "运行 python scripts/download_models.py --h3 下载。"
                )

            on_progress(
                0.02,
                f"加载 MiniMax H3（{h3_quant.TIER_LABEL.get(plan.tier, plan.tier)}，"
                "首次约 2~5 分钟）",
            )
            pipe = ModularPipeline.from_pretrained(
                MODEL_REPO, workflow=workflow, cache_dir=str(HF_CACHE)
            )
            load_kwargs = dict(
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
                cache_dir=str(HF_CACHE),
            )
            # 量化必须走 from_pretrained 的 quantization_config。
            # 旧写的 load_kwargs["fp8"] 不是 from_pretrained 的参数，会被
            # 当成 config 覆盖项静默吞掉——等于从未生效。
            quant_config = h3_quant.build_quantization_config(plan.quantization)
            if quant_config:
                load_kwargs["quantization_config"] = quant_config
            pipe.load_components(**load_kwargs)

            on_progress(0.25, "布置权重与卸载策略")
            # 文本编码器（Qwen3-VL 32B）：永远叶子级卸载，不进显存常驻
            apply_group_offloading(
                pipe.text_encoder, onload_device="cuda",
                offload_type="leaf_level", use_stream=True,
            )
            # VAE：两个都走 CPU 卸载钩子，串成链避免同时占显存
            try:
                from accelerate import cpu_offload_with_hook
                _, vae = cpu_offload_with_hook(pipe.vae, execution_device="cuda")
                if getattr(pipe, "audio_vae", None) is not None:
                    cpu_offload_with_hook(
                        pipe.audio_vae, execution_device="cuda", prev_module_hook=vae
                    )
            except Exception as exc:  # 无 audio_vae 或 accelerate 版本差异
                print(f"[h3] VAE 卸载链跳过：{exc}")
            # 低显存档：VAE 分块解码，激活不再一次性铺满显存
            if plan.max_pixels < h3_quant.PIXELS_DEFAULT:
                for name in ("vae", "audio_vae"):
                    comp = getattr(pipe, name, None)
                    enable = getattr(comp, "enable_tiling", None)
                    if callable(enable):
                        try:
                            enable()
                        except Exception as exc:
                            print(f"[h3] {name} 分块解码未启用：{exc}")
            # 主 transformer
            if strategy["offload"] == "block_level":
                apply_group_offloading(
                    pipe.transformer, onload_device="cuda",
                    offload_type="block_level", num_blocks_per_group=1, use_stream=True,
                )
            else:
                pipe.transformer.to("cuda")

            self._pipe = pipe
            self._pipe_workflow = workflow
            on_progress(0.45, "管线就绪")
            return self._pipe

    # ---------- 帧数/分辨率归一 ----------
    @staticmethod
    def normalize_num_frames(num_frames: int, fps: int) -> int:
        """把「按请求 fps 的帧数」折算成 H3 的 24fps 帧数，并吸附到 17n+5。

        模型要求时长落在 5~15 秒，短于一帧也算 5 秒（之后按请求时长截断回来）。
        """
        target = round(max(num_frames, 1) / max(fps, 1) * H3_FPS)
        target = min(max(target, MIN_FRAMES), MAX_FRAMES)
        n = max(1, math.ceil((target - 5) / 17))
        return 17 * n + 5

    @staticmethod
    def normalize_size(width: int, height: int, max_pixels: int = h3_quant.PIXELS_DEFAULT) -> tuple:
        """宽高取 32 的倍数（模型硬约束），并限制在画布上限内。

        max_pixels 由量化档位给出：8GB 显存档会压到 512×384 级，
        因为按块卸载时显存主要被激活占满，而帧数有 124 帧的下限。
        """
        w = max(256, int(round(width / 32.0)) * 32)
        h = max(256, int(round(height / 32.0)) * 32)
        ceiling = max(int(max_pixels or h3_quant.PIXELS_DEFAULT), 256 * 256)
        pixels = w * h
        if pixels > ceiling:
            scale = math.sqrt(ceiling / pixels)
            w = max(256, int(w * scale // 32 * 32))
            h = max(256, int(h * scale // 32 * 32))
        return w, h

    @staticmethod
    def resolve_workflow(first_frame: str = "", last_frame: str = "") -> tuple:
        """按可用关键帧决定 workflow 与 pipeline 的图片参数。

        - 无关键帧 → t2va（纯文）
        - 有首帧 / 尾帧 / 两者 → fl2va（同一套权重，参数名为 image / last_image）
        """
        if not first_frame and last_frame:
            return "fl2va", {"last_image": last_frame}
        if first_frame:
            keyframes = {"image": first_frame}
            if last_frame:
                keyframes["last_image"] = last_frame
            return "fl2va", keyframes
        return "t2va", {}

    @staticmethod
    def resample_frames(native: list, num_frames: int, fps: int) -> list:
        """把 H3 的原生 24fps 帧序列转成「请求帧数 @ 请求 fps」。

        模型最少出 5 秒，但分镜镜头常只有 2~4 秒：先按请求时长截断
        （``num_frames/fps*24`` 帧），再等间隔抽成 num_frames 帧——
        队列按 fps 合成后时长与请求一致，不会把 2 秒镜头拖成 5 秒。
        """
        if not native:
            return []
        want = max(1, min(len(native), round(num_frames / max(fps, 1) * H3_FPS)))
        clipped = native[:want]
        if num_frames <= 1 or len(clipped) == 1:
            return [clipped[0]] * max(num_frames, 1)
        span = len(clipped) - 1
        return [
            clipped[min(span, round(i * span / (num_frames - 1)))]
            for i in range(num_frames)
        ]

    @staticmethod
    def _extract_frames(videos) -> list:
        """把 pipeline 输出统一成 PIL 帧列表（兼容 batch/单条/PIL/ndarray）。"""
        from PIL import Image

        frames = videos
        if isinstance(frames, (list, tuple)) and frames and isinstance(frames[0], (list, tuple)):
            frames = frames[0]
        out = []
        for item in frames:
            if isinstance(item, Image.Image):
                out.append(item)
            else:
                import numpy as np
                arr = np.asarray(item)
                if arr.dtype != np.uint8:
                    arr = (np.clip(arr, 0.0, 1.0) * 255).round().astype("uint8") \
                        if arr.max() <= 1.0 else arr.astype("uint8")
                out.append(Image.fromarray(arr))
        return out

    # ---------- 生成 ----------
    def generate(self, *, prompt, negative_prompt, width, height, num_frames,
                 fps, steps, guidance, seed, frames_dir: Path, on_progress,
                 is_cancelled, reference_image: str = "",
                 first_frame_image: str = "", last_frame_image: str = "",
                 resume_prompt_id: str = "", workflow_snapshot: str = "",
                 on_submitted=None, resume_client_id: str = "") -> List[Path]:
        """原生推理。ComfyUI 专用参数（resume_*/workflow_snapshot/on_submitted）忽略。"""
        import random

        import torch
        from PIL import Image

        if seed < 0:
            seed = random.randint(0, 2**31 - 1)

        # ---- 图片条件：首帧/尾帧（continuation 用尾帧）----
        first = first_frame_image or reference_image
        workflow, keyframes = self.resolve_workflow(first, last_frame_image)

        h3_frames = self.normalize_num_frames(num_frames, fps)
        # 画布上限跟量化档位走：8GB 档压到 512×384 级，避免激活撑爆
        plan = self._strategy(hw_probe.probe())["plan"]
        w, h = self.normalize_size(width, height, plan.max_pixels)
        if (w, h) != self.normalize_size(width, height):
            on_progress(0.47, f"画布按本机档位缩到 {w}×{h}")

        pipe = self._get_pipe(workflow, on_progress)

        # 关键帧读成 PIL（pipeline 不会自己开文件）
        kwargs = {}
        for key, path in keyframes.items():
            try:
                kwargs[key] = Image.open(path).convert("RGB")
            except Exception as exc:
                raise ValueError(f"首/尾帧图片读取失败（{key}）: {exc}") from exc

        if is_cancelled():
            raise GenerationCancelled()

        torch.manual_seed(seed)
        on_progress(0.48, f"去噪中（{H3_STEPS - 1} 步，{w}×{h} / {h3_frames} 帧）")
        # H3 是 guidance 蒸馏模型：不吃 negative_prompt / guidance_scale
        with torch.no_grad():
            result = pipe(
                prompt=prompt,
                width=w, height=h,
                num_frames=h3_frames,
                num_inference_steps=H3_STEPS,
                output_type="pil",
                output=["videos"],
                **kwargs,
            )
        # modular blocks 没有 step 回调：生成期间无法中断，只能事后检查
        if is_cancelled():
            raise GenerationCancelled()

        on_progress(0.9, "解码并整理帧")
        native = self._extract_frames(getattr(result, "videos", result))
        if not native:
            raise RuntimeError("H3 未返回任何帧")

        # ---- 按请求时长截断 + 重采样到请求 fps ----
        picked = self.resample_frames(native, num_frames, fps)
        if not picked:
            raise RuntimeError("H3 输出经重采样后为空")

        frames_dir.mkdir(parents=True, exist_ok=True)
        paths: List[Path] = []
        for i, frame in enumerate(picked):
            p = frames_dir / f"frame_{i:04d}.png"
            frame.save(p)
            paths.append(p)

        on_progress(1.0, f"{len(paths)} 帧已生成（H3 原生 {h3_frames} 帧 @24fps）")
        return paths
