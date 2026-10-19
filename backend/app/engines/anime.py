"""AnimeDiff 动漫引擎：动漫底模 + AnimateDiff v3 运动模块

硬件自适应：有 NVIDIA 显卡走 CUDA + fp16，否则 CPU + fp32，换机器不用改代码。

内存策略（重点，低内存机器的关键）：
- AnimateDiff 的标准加载方式 from_pipe() 会在内存里同时保留「原始 UNet + 新建的
  motion UNet」，fp32 下峰值约 7.8GB——内存紧张的机器会直接段错误。
- 本引擎改为：首次加载时现场把运动模块合并进 UNet，得到 UNetMotionModel 后
  save_pretrained 落盘（models/anime-motion-unet/），之后所有加载走快速路径：
  直接 from_pretrained 预转换权重，motion_adapter 传 None（权重已在 unet 内），
  峰值约 4.2GB。
- 转换过程本身也用低内存手法：mmap 加载（low_cpu_mem_usage）、不在转换阶段加载
  text_encoder/vae、转完立即 del 源头并 gc。

注意：不走 from_single_file（单文件 ckpt）——本环境加载 9GB 级 safetensors 会触发
原生段错误，且需要从 hub 拉管线配置。diffusers 格式底模 + from_pretrained 稳。
"""
import gc
import random
import threading
from pathlib import Path
from typing import List

from .base import BaseEngine, EngineMeta, EngineUnavailable, GenerationCancelled
from ..config import MODELS_DIR

MOTION_ADAPTER_DIR = MODELS_DIR / "animatediff-motion-v3"
ANIME_MODEL_DIR = MODELS_DIR / "anime-diffusers"
# 预转换权重：AnimateDiff 运动模块合并进 UNet 后的独立模型（首次加载自动生成）
PREBUILT_UNET_DIR = MODELS_DIR / "anime-motion-unet"


class AnimeEngine(BaseEngine):
    meta = EngineMeta(
        id="anime",
        name="AnimeDiff 动漫引擎",
        description="dreamlike-anime 动漫底模 + AnimateDiff v3 · CPU/GPU 自适应 · 512x512",
        status="ready",
    )

    _pipe = None
    _device = "cpu"
    _dtype = None
    _load_lock = threading.Lock()

    # ---------- 模型加载（懒加载 + 单例缓存） ----------
    def _get_pipe(self, on_progress):
        with self._load_lock:
            if self._pipe is not None:
                return self._pipe

            import torch
            from diffusers import AnimateDiffPipeline, AutoencoderKL, DDIMScheduler
            from diffusers.models import UNet2DConditionModel, UNetMotionModel
            from transformers import CLIPTextModel, CLIPTokenizer

            if not (ANIME_MODEL_DIR / "model_index.json").exists():
                raise EngineUnavailable(
                    f"未找到动漫底模（{ANIME_MODEL_DIR}），"
                    "请先运行: python scripts/download_models.py"
                )

            # 硬件自适应：有 NVIDIA 显卡用 CUDA+fp16，否则 CPU+fp32
            # （CPU 上 fp16 不会加速，反而可能更慢）
            if torch.cuda.is_available():
                self._device, self._dtype = "cuda", torch.float16
            else:
                self._device, self._dtype = "cpu", torch.float32

            # ---- UNet：快速路径（预转换权重）或现场转换（仅首次） ----
            if (PREBUILT_UNET_DIR / "config.json").exists():
                on_progress(0.02, f"加载动漫 UNet（预转换，{self._device}）")
                unet = UNetMotionModel.from_pretrained(
                    str(PREBUILT_UNET_DIR),
                    torch_dtype=self._dtype,
                    low_cpu_mem_usage=True,
                )
            else:
                if not MOTION_ADAPTER_DIR.exists():
                    raise EngineUnavailable(
                        f"未找到 AnimateDiff 运动模块（{MOTION_ADAPTER_DIR}），"
                        "请先运行: python scripts/download_models.py"
                    )
                on_progress(0.02, "合并 AnimateDiff 运动模块（首次约 1 分钟）")
                from diffusers import MotionAdapter

                # mmap 加载：文件页可被系统回收，避免匿名内存峰值
                adapter = MotionAdapter.from_pretrained(
                    str(MOTION_ADAPTER_DIR), torch_dtype=torch.float32, low_cpu_mem_usage=True
                )
                base_unet = UNet2DConditionModel.from_pretrained(
                    str(ANIME_MODEL_DIR), subfolder="unet",
                    torch_dtype=torch.float32, low_cpu_mem_usage=True,
                )
                unet = UNetMotionModel.from_unet2d(base_unet, motion_adapter=adapter, load_weights=True)
                # 源头不再需要，立即释放——低内存机器的关键一步
                del base_unet, adapter
                gc.collect()

                on_progress(0.06, "保存预转换权重（仅首次）")
                unet.save_pretrained(str(PREBUILT_UNET_DIR))
                if self._dtype is not torch.float32:
                    unet = unet.to(self._dtype)

            on_progress(0.08, "加载文本编码器与 VAE")
            tok = CLIPTokenizer.from_pretrained(str(ANIME_MODEL_DIR), subfolder="tokenizer")
            te = CLIPTextModel.from_pretrained(
                str(ANIME_MODEL_DIR), subfolder="text_encoder", torch_dtype=self._dtype
            )
            vae = AutoencoderKL.from_pretrained(
                str(ANIME_MODEL_DIR), subfolder="vae", torch_dtype=self._dtype
            )
            scheduler = DDIMScheduler.from_pretrained(str(ANIME_MODEL_DIR), subfolder="scheduler")

            on_progress(0.09, "组装推理管线")
            pipe = AnimateDiffPipeline(
                vae=vae,
                text_encoder=te,
                tokenizer=tok,
                unet=unet,
                motion_adapter=None,  # 权重已合并进 unet，推理不依赖此组件
                scheduler=scheduler,
                feature_extractor=None,
                image_encoder=None,
            )
            pipe = pipe.to(self._device)
            if self._device == "cuda":
                pipe.enable_attention_slicing()  # 显存不足时自动切片

            self._pipe = pipe
            on_progress(0.10, "管线就绪")
            return self._pipe

    # ---------- 生成 ----------
    def generate(self, *, prompt, negative_prompt, width, height, num_frames,
                 fps, steps, guidance, seed, frames_dir, on_progress, is_cancelled,
                 reference_image: str = "", first_frame_image: str = "",
                 last_frame_image: str = "", resume_prompt_id: str = "",
                 workflow_snapshot: str = "", on_submitted=None,
                 resume_client_id: str = "") -> List[Path]:
        # AnimateDiff 运动模块仅支持文生视频；reference_image 忽略（续接靠提示词衔接）
        # resume_prompt_id / workflow_snapshot / on_submitted / resume_client_id
        # 为 ComfyUI 专用参数，本引擎忽略
        import torch

        if seed < 0:
            seed = random.randint(0, 2**31 - 1)

        # 参数护栏：AnimateDiff 是 SD1.5 级模型，大参数会 OOM（实测 832x480 81帧
        # 单次分配 9.5GB 直接崩）。超出甜点范围快速失败，引导用 Wan 2.2 引擎。
        if num_frames > 32 or width * height > 640 * 640:
            raise EngineUnavailable(
                f"参数超出 AnimeDiff 引擎建议范围（{width}×{height} / {num_frames} 帧）："
                "SD1.5 级模型 >32 帧或 >640×640 会内存溢出。"
                "请降低参数，或改用 Wan 2.2 引擎（8GB 显存可跑 720p）。"
            )

        # AnimateDiff 约束：偶数帧（8/16...）、边长 8 的倍数（SD VAE 压缩率）
        num_frames = max(8, (num_frames // 2) * 2)
        width = max(256, (width // 8) * 8)
        height = max(256, (height // 8) * 8)

        pipe = self._get_pipe(on_progress)
        generator = torch.Generator(device=self._device).manual_seed(seed)

        def callback(step, timestep, latents):
            if is_cancelled():
                raise GenerationCancelled("用户取消")
            frac = (step + 1) / max(steps, 1)
            on_progress(0.10 + 0.80 * frac, f"去噪中 {step + 1}/{steps} 步")

        with torch.no_grad():
            result = pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                height=height,
                width=width,
                num_frames=num_frames,
                num_inference_steps=steps,
                guidance_scale=guidance,
                generator=generator,
                output_type="pil",
                callback=callback,
                callback_steps=1,
                decode_chunk_size=8,  # 限制 VAE 单批解码帧数，压低峰值内存
            )

        frames = result.frames[0]
        frames_dir.mkdir(parents=True, exist_ok=True)
        paths: List[Path] = []
        for i, frame in enumerate(frames):
            p = frames_dir / f"frame_{i:04d}.png"
            frame.save(p)
            paths.append(p)

        on_progress(0.90, f"{len(paths)} 帧已生成")
        return paths
