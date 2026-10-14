"""验证 diffusers 格式动漫模型能否在 CPU 上完整加载。"""
import sys
import time
from pathlib import Path

import faulthandler
import torch

faulthandler.enable()

BACKEND = Path(__file__).resolve().parents[1]
MODEL_DIR = BACKEND / "models" / "anime-diffusers"
MOTION_DIR = BACKEND / "models" / "animatediff-motion-v3"


def step(label):
    print(f"\n>>> {label}", flush=True)
    t = time.time()
    return t


def main():
    from diffusers import (
        AnimateDiffPipeline,
        AutoencoderKL,
        MotionAdapter,
        StableDiffusionPipeline,
        UNet2DConditionModel,
    )
    from transformers import CLIPTextModel, CLIPTokenizer

    t = step("tokenizer")
    tok = CLIPTokenizer.from_pretrained(str(MODEL_DIR), subfolder="tokenizer")
    print(f"    vocab={len(tok)} ({time.time()-t:.1f}s)", flush=True)

    t = step("text_encoder")
    te = CLIPTextModel.from_pretrained(str(MODEL_DIR), subfolder="text_encoder", torch_dtype=torch.float32)
    print(f"    loaded ({time.time()-t:.1f}s)", flush=True)

    t = step("unet")
    unet = UNet2DConditionModel.from_pretrained(str(MODEL_DIR), subfolder="unet", torch_dtype=torch.float32)
    print(f"    loaded ({time.time()-t:.1f}s)", flush=True)

    t = step("vae")
    vae = AutoencoderKL.from_pretrained(str(MODEL_DIR), subfolder="vae", torch_dtype=torch.float32)
    print(f"    loaded ({time.time()-t:.1f}s)", flush=True)

    t = step("motion_adapter")
    adapter = MotionAdapter.from_pretrained(str(MOTION_DIR), torch_dtype=torch.float32)
    print(f"    loaded ({time.time()-t:.1f}s)", flush=True)

    t = step("compose pipeline")
    pipe = AnimateDiffPipeline.from_pipe(
        StableDiffusionPipeline(
            vae=vae,
            text_encoder=te,
            tokenizer=tok,
            unet=unet,
            scheduler=pipe_scheduler(),
            safety_checker=None,
            feature_extractor=None,
            requires_safety_checker=False,
        ),
        motion_adapter=adapter,
    )
    print(f"    pipeline ready ({time.time()-t:.1f}s)", flush=True)

    print("\n✅ 全部加载成功", flush=True)


def pipe_scheduler():
    from diffusers import DDIMScheduler

    return DDIMScheduler.from_pretrained(str(MODEL_DIR), subfolder="scheduler")


if __name__ == "__main__":
    import torch

    sys.exit(main())