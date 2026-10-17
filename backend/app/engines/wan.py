"""Wan 2.2 文生视频引擎（FreeVideo 风格低显存实现）

目标硬件线：**8GB 显存 + 32GB 内存**（FreeVideo 同款定位）。
核心技术：
  1. 模型选择：TI2V-5B 为低配默认（bf16 权重约 10GB）；T2V-A14B 双专家供
     60GB+ 内存 + 20GB+ 显存的机器（画质标杆）
  2. 量化：40 系+ fp8 动态量化，30 系 int8 weight-only（torchao，可选依赖）
  3. Offload：显存 <12GB 走 sequential CPU offload（逐层流式，8GB 可跑）
  4. VAE tiling：压低 720p 解码峰值
  5. MoE：A14B 双专家由 pipeline 内部按 boundary_ratio 切换

模型下载（hf-mirror）：
  Wan-AI/Wan2.2-TI2V-5B-Diffusers  → models/wan-ti2v-5b/   （约 32GB）
  Wan-AI/Wan2.2-T2V-A14B-Diffusers → models/wan-t2v-a14b/  （约 119GB，按需）
"""
import threading
from pathlib import Path
from typing import List

from .base import BaseEngine, EngineMeta, EngineUnavailable, GenerationCancelled
from ..config import MODELS_DIR
from .. import hw_probe

MODEL_DIRS = {
    "ti2v-5b": MODELS_DIR / "wan-ti2v-5b",
    "t2v-a14b": MODELS_DIR / "wan-t2v-a14b",
}
MODEL_REPOS = {
    "ti2v-5b": "Wan-AI/Wan2.2-TI2V-5B-Diffusers",
    "t2v-a14b": "Wan-AI/Wan2.2-T2V-A14B-Diffusers",
}


class WanEngine(BaseEngine):
    meta = EngineMeta(
        id="wan22",
        name="Wan 2.2 文生视频",
        description="开源画质标杆 · 8GB 显存可跑（offload+量化）· 最高 720p",
        status="ready",
    )

    _pipe = None
    _profile: hw_probe.HardwareProfile = None
    _load_lock = threading.Lock()

    # ---------- 动态状态（模型是否已下载 + 硬件是否支持） ----------
    def describe(self) -> dict:
        profile = hw_probe.probe()
        model_dir = MODEL_DIRS[profile.model_id]
        has_model = (model_dir / "model_index.json").exists()
        if not has_model:
            status, note = "unavailable", (
                f"未找到模型（{model_dir}）。下载命令："
                f"HF_ENDPOINT=https://hf-mirror.com huggingface-cli download "
                f"{MODEL_REPOS[profile.model_id]} --local-dir {model_dir}"
            )
        elif profile.device == "cpu":
            status, note = "ready", "CPU 可跑但极慢（小时级），建议先用 AnimeDiff 引擎"
        else:
            status, note = "ready", "；".join(profile.notes) or "就绪"
        return {
            "id": self.meta.id,
            "name": self.meta.name,
            "description": self.meta.description,
            "status": status,
            "note": note,
            "hardware": profile.to_dict(),
        }

    # ---------- 模型加载（懒加载 + 单例缓存） ----------
    def _get_pipe(self, on_progress):
        with self._load_lock:
            if self._pipe is not None:
                return self._pipe

            import torch
            from diffusers import WanPipeline

            profile = hw_probe.probe()
            self._profile = profile
            model_dir = MODEL_DIRS[profile.model_id]
            if not (model_dir / "model_index.json").exists():
                raise EngineUnavailable(self.describe()["note"])

            on_progress(0.02, f"加载 Wan 2.2（{profile.model_id}，{profile.device}）")
            pipe = WanPipeline.from_pretrained(
                str(model_dir),
                torch_dtype=torch.bfloat16 if profile.device == "cuda" else torch.float32,
            )

            # ---- 量化（仅 CUDA；CPU 上 fp32/bf16 已是甜点） ----
            if profile.device == "cuda" and profile.quant in ("fp8", "int8"):
                on_progress(0.30, f"量化权重（{profile.quant}，约 1-2 分钟）")
                self._quantize(pipe, profile.quant)

            # ---- Offload 链：核心低显存机制 ----
            if profile.offload == "sequential":
                on_progress(0.55, "启用逐层显存卸载（8GB 模式，较慢）")
                pipe.enable_sequential_cpu_offload()
            elif profile.offload == "model":
                on_progress(0.55, "启用模型级显存卸载")
                pipe.enable_model_cpu_offload()
            else:
                pipe = pipe.to(profile.device)

            # ---- VAE tiling：压低 720p 解码峰值 ----
            if profile.vae_tiling:
                pipe.enable_vae_tiling()

            self._pipe = pipe
            on_progress(0.60, "管线就绪")
            return self._pipe

    # ---------- torchao 量化 ----------
    @staticmethod
    def _quantize(pipe, mode: str):
        """对 transformer（+text_encoder）做权重量化。torchao 缺失或失败则静默跳过。"""
        try:
            import torch.nn as nn
            from torchao.quantization import quantize_

            # torchao 0.18 用类式 Config；更新版提供函数式快捷方式，两者都试
            if mode == "fp8":
                try:
                    from torchao.quantization import Float8DynamicActivationFloat8WeightConfig as C
                    cfg = C()
                except ImportError:
                    from torchao.quantization import float8_dynamic_act_float8_weight as f
                    cfg = f()
            else:
                try:
                    from torchao.quantization import Int8WeightOnlyConfig as C
                    cfg = C()
                except ImportError:
                    from torchao.quantization import int8_weight_only as f
                    cfg = f()

            def _filt(m, fqn):
                # 只量化大 Linear；维度对齐 16 保证 int8/fp8 kernel 可用
                return (
                    isinstance(m, nn.Linear)
                    and m.in_features % 16 == 0
                    and m.out_features % 16 == 0
                    and min(m.in_features, m.out_features) >= 256
                )

            targets = [pipe.transformer]
            if getattr(pipe, "transformer_2", None) is not None:
                targets.append(pipe.transformer_2)
            te = getattr(pipe, "text_encoder", None)
            if te is not None:
                targets.append(te)

            for t in targets:
                quantize_(t, cfg, filter_fn=_filt)
        except Exception as e:  # 量化失败不阻断，退回 bf16
            print(f"[wan] 量化跳过（{mode}）：{e}")

    # ---------- 生成 ----------
    def generate(self, *, prompt, negative_prompt, width, height, num_frames,
                 fps, steps, guidance, seed, frames_dir, on_progress, is_cancelled,
                 reference_image: str = "", resume_prompt_id: str = "",
                 workflow_snapshot: str = "") -> List[Path]:
        # Wan2.2-TI2V v1 仅文生视频；reference_image 忽略（首尾帧走 ComfyUI 工作流）
        # resume_prompt_id / workflow_snapshot 为 ComfyUI 专用参数，本引擎忽略
        import random

        import torch

        if seed < 0:
            seed = random.randint(0, 2**31 - 1)

        # Wan 约束：帧数 4n+1；边长 16 的倍数
        num_frames = 4 * ((num_frames - 1) // 4) + 1
        width = max(128, (width // 16) * 16)
        height = max(128, (height // 16) * 16)

        pipe = self._get_pipe(on_progress)
        generator = torch.Generator(device="cpu").manual_seed(seed)

        def _on_step_end(pipe_, i, t, callback_kwargs):
            if is_cancelled():
                raise GenerationCancelled("用户取消")
            frac = (i + 1) / max(steps, 1)
            on_progress(0.60 + 0.35 * frac, f"去噪中 {i + 1}/{steps} 步")
            return callback_kwargs

        call_kwargs = dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=generator,
            output_type="pil",
            callback_on_step_end=_on_step_end,
            callback_on_step_end_tensor_inputs=["latents"],
        )
        # A14B 双专家：第二个专家用同一 guidance
        if getattr(pipe, "transformer_2", None) is not None:
            call_kwargs["guidance_scale_2"] = guidance

        with torch.no_grad():
            result = pipe(**call_kwargs)

        on_progress(0.96, "解码并保存帧")
        frames = result.frames[0]
        frames_dir.mkdir(parents=True, exist_ok=True)
        paths: List[Path] = []
        for i, frame in enumerate(frames):
            p = frames_dir / f"frame_{i:04d}.png"
            frame.save(p)
            paths.append(p)

        on_progress(1.0, f"{len(paths)} 帧已生成")
        return paths
