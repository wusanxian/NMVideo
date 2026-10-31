"""Wan 2.2 文生视频引擎（低显存实现）

目标硬件线：**8GB 显存 + 32GB 内存**。
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
        # 兜底通道：进程内推理，仅 ComfyUI 不可用时使用（非默认引擎）
        execution_channel="native",
    )

    _pipe = None
    _profile: hw_probe.HardwareProfile = None
    _load_lock = threading.Lock()
    # 低内存机型（可用内存装不下「DiT + 文本编码器」两份权重）走分阶段加载：
    # pipe 里只有 transformer + vae，文本编码器单独懒加载、编码完立即释放。
    _staged = False
    _te = None                 # 懒加载的 UMT5EncoderModel（仅分阶段模式用）
    _tok = None
    _embeds_key = None         # 缓存键：(prompt, negative_prompt)
    _embeds_val = None         # (prompt_embeds, negative_prompt_embeds)

    def unload(self):
        from .local_runtime import unload_pipeline
        unload_pipeline(self)
        # 分阶段模式的缓存也要一起清：否则重新加载会沿用旧的 embeds/文本编码器
        self._staged = False
        self._te = None
        self._tok = None
        self._embeds_key = None
        self._embeds_val = None

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
    # 可用内存低于这个值就走分阶段加载：DiT（5B·fp32 加载≈19GB）和 T5 文本编码器
    # （≈11GB）两份权重同时常驻要 30GB 以上，32GB 内存的机器（实际可用 ~22GB）
    # 会在加载 T5 时被 OOM 直接杀掉进程（safetensors 报 "memory allocation failed"）。
    STAGED_RAM_BELOW_GB = 36.0

    def _get_pipe(self, on_progress):
        with self._load_lock:
            if self._pipe is not None:
                return self._pipe

            import torch

            profile = hw_probe.probe()
            self._profile = profile
            model_dir = MODEL_DIRS[profile.model_id]
            if not (model_dir / "model_index.json").exists():
                raise EngineUnavailable(self.describe()["note"])

            avail = profile.available_ram_gb or profile.ram_gb or 0.0
            staged = profile.device == "cuda" and 0 < avail < self.STAGED_RAM_BELOW_GB
            tail = "，分阶段省内存" if staged else ""
            on_progress(0.02, f"加载 Wan 2.2（{profile.model_id}，{profile.device}{tail}）")

            if staged:
                pipe = self._load_staged(model_dir, profile, on_progress)
            else:
                pipe = self._load_full(model_dir, profile, on_progress)

            self._staged = staged
            self._pipe = pipe
            on_progress(0.60, "管线就绪")
            return self._pipe

    def _apply_offload(self, pipe, profile, on_progress):
        """Offload 链：显存不够时逐层在 CPU/GPU 之间搬，是 8GB 卡能跑的关键。"""
        if profile.offload == "sequential":
            on_progress(0.55, "启用逐层显存卸载（8GB 模式，较慢）")
            pipe.enable_sequential_cpu_offload()
        elif profile.offload == "model":
            on_progress(0.55, "启用模型级显存卸载")
            pipe.enable_model_cpu_offload()
        else:
            pipe = pipe.to(profile.device)
        # tiling 是 VAE 自己的能力，不在 WanPipeline 上：调 pipe.enable_vae_tiling()
        # 会直接 AttributeError，而 vae_tiling 默认就是 True —— 等于每次加载必崩。
        if profile.vae_tiling and getattr(pipe, "vae", None) is not None:
            pipe.vae.enable_tiling()
        return pipe

    def _load_full(self, model_dir, profile, on_progress):
        """内存充足：一次 from_pretrained 拿全套组件（简单、快）。"""
        import torch
        from diffusers import WanPipeline

        pipe = WanPipeline.from_pretrained(
            str(model_dir),
            # 参数名叫 dtype：新版 diffusers 已弃用 torch_dtype（旧名仍接受但会告警）
            dtype=torch.bfloat16 if profile.device == "cuda" else torch.float32,
        )
        if profile.device == "cuda" and profile.quant in ("fp8", "int8"):
            on_progress(0.30, f"量化权重（{profile.quant}，约 1-2 分钟）")
            profile.quant = self._quantize(pipe, profile.quant)
        return self._apply_offload(pipe, profile, on_progress)

    def _load_staged(self, model_dir, profile, on_progress):
        """内存紧张：只加载 DiT + VAE，文本编码器在生成时临时加载、用完即弃。

        两条硬事实决定了必须这么拆：
          1. diffusers 的 dtype/torch_dtype 参数对分片权重**不生效**，实测加载完
             仍是 fp32（5B 参数占 18.6GB）。必须在加载后显式 to(bf16)，逐参数
             原地转换，峰值只多一个参数块。
          2. T5 文本编码器 11GB 与 DiT 不能共存。先把 prompt 编码成 embeds，
             释放 T5，再上采样，峰值就从 33GB 压到 19GB。
        """
        import torch
        from diffusers import AutoencoderKLWan, WanPipeline, WanTransformer3DModel
        from diffusers.schedulers.scheduling_flow_match_euler_discrete import FlowMatchEulerDiscreteScheduler

        dtype = torch.bfloat16
        on_progress(0.05, "加载扩散主干（约 19GB，首次较慢）")
        tr = WanTransformer3DModel.from_pretrained(str(model_dir), subfolder="transformer", dtype=dtype)
        if next(tr.parameters()).dtype != dtype:
            on_progress(0.28, "主干权重转 bf16（省一半内存）")
            tr = tr.to(dtype)
        on_progress(0.35, "加载 VAE 与调度器")
        vae = AutoencoderKLWan.from_pretrained(str(model_dir), subfolder="vae", dtype=dtype)
        sched = FlowMatchEulerDiscreteScheduler.from_pretrained(str(model_dir), subfolder="scheduler")
        pipe = WanPipeline(tokenizer=None, text_encoder=None, vae=vae, scheduler=sched, transformer=tr)
        return self._apply_offload(pipe, profile, on_progress)

    def _encode_prompt(self, prompt, negative_prompt, on_progress):
        """返回 (prompt_embeds, negative_prompt_embeds)；全量模式返回 (None, None)。

        分阶段模式下文本编码器不常驻：编码完立刻删掉并回收，把 11GB 还给系统。
        编码结果按 (prompt, negative) 缓存，同一提示词重复出片不用再加载一次。
        """
        if not self._staged:
            return None, None

        import gc

        import torch

        key = (prompt or "", negative_prompt or "")
        if self._embeds_key == key and self._embeds_val is not None:
            return self._embeds_val

        from transformers import AutoTokenizer, UMT5EncoderModel

        model_dir = MODEL_DIRS[self._profile.model_id]
        dtype = torch.bfloat16
        max_seq = 226

        on_progress(0.61, "加载文本编码器（首次约 1 分钟）")
        if self._tok is None:
            self._tok = AutoTokenizer.from_pretrained(str(model_dir), subfolder="tokenizer")
        te = UMT5EncoderModel.from_pretrained(str(model_dir), subfolder="text_encoder", dtype=dtype)
        if next(te.parameters()).dtype != dtype:
            te = te.to(dtype)
        te.eval()

        def _run(text):
            tokens = self._tok(
                [text], padding="max_length", max_length=max_seq, truncation=True,
                add_special_tokens=True, return_attention_mask=True, return_tensors="pt",
            )
            ids, mask = tokens.input_ids, tokens.attention_mask
            seq = mask.gt(0).sum(dim=1).long()
            with torch.no_grad():
                emb = te(ids, mask).last_hidden_state.to(dtype=dtype, device="cpu")
            emb = [u[:v] for u, v in zip(emb, seq)]
            return torch.stack(
                [torch.cat([u, u.new_zeros(max_seq - u.size(0), u.size(1))]) for u in emb], dim=0
            )

        on_progress(0.62, "编码提示词")
        p_emb = _run(prompt or "")
        n_emb = _run(negative_prompt) if negative_prompt is not None else None
        del te
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self._embeds_key = key
        self._embeds_val = (p_emb, n_emb)
        return p_emb, n_emb

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
            return mode
        except Exception as e:  # 量化失败不阻断，退回 bf16
            print(f"[wan] 量化跳过（{mode}）：{e}")
            return "partial_or_failed"

    # ---------- 生成 ----------
    def generate(self, *, prompt, negative_prompt, width, height, num_frames,
                 fps, steps, guidance, seed, frames_dir, on_progress, is_cancelled,
                 reference_image: str = "", first_frame_image: str = "",
                 last_frame_image: str = "", resume_prompt_id: str = "",
                 workflow_snapshot: str = "", on_submitted=None,
                 resume_client_id: str = "") -> List[Path]:
        # Wan2.2-TI2V v1 仅文生视频；reference_image 忽略（首尾帧走 ComfyUI 工作流）
        # resume_prompt_id / workflow_snapshot / on_submitted / resume_client_id
        # 为 ComfyUI 专用参数，本引擎忽略
        import random

        import torch

        if seed < 0:
            seed = random.randint(0, 2**31 - 1)

        # Wan 约束：帧数 4n+1；边长 16 的倍数
        num_frames = 4 * ((num_frames - 1) // 4) + 1
        width = max(128, (width // 16) * 16)
        height = max(128, (height // 16) * 16)

        pipe = self._get_pipe(on_progress)
        # 分阶段模式：文本编码器临时加载，编码完即释放；返回 embeds 直接进采样
        p_emb, n_emb = self._encode_prompt(prompt, negative_prompt, on_progress)
        generator = torch.Generator(device="cpu").manual_seed(seed)

        # 采样进度区间的起点：分阶段模式里编码占了 0.61~0.65，别让进度条倒退
        base = 0.66 if p_emb is not None else 0.60

        def _on_step_end(pipe_, i, t, callback_kwargs):
            if is_cancelled():
                raise GenerationCancelled("用户取消")
            frac = (i + 1) / max(steps, 1)
            on_progress(base + (0.96 - base) * frac, f"去噪中 {i + 1}/{steps} 步")
            return callback_kwargs

        call_kwargs = dict(
            prompt=prompt if p_emb is None else None,
            negative_prompt=negative_prompt if p_emb is None else None,
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
        if p_emb is not None:
            call_kwargs["prompt_embeds"] = p_emb
            if n_emb is not None:
                call_kwargs["negative_prompt_embeds"] = n_emb
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
