"""实测 AnimateDiffPipeline 组装过程的内存曲线，定位峰值点。"""
import gc
import os
import threading
import time
from pathlib import Path

import psutil

BACKEND = Path(__file__).resolve().parents[1]
PROC = psutil.Process(os.getpid())
peak = {"rss": 0, "at": ""}
stop = threading.Event()


def sampler():
    while not stop.is_set():
        rss = PROC.memory_info().rss / 2**30
        if rss > peak["rss"]:
            peak["rss"] = rss
            peak["at"] = time.strftime("%H:%M:%S")
        stop.wait(0.25)


def mark(label):
    rss = PROC.memory_info().rss / 2**30
    print(f"[{time.strftime('%H:%M:%S')}] {label:<28} RSS={rss:.2f} GB", flush=True)


def main():
    import torch
    from diffusers import AnimateDiffPipeline, MotionAdapter, StableDiffusionPipeline

    t = threading.Thread(target=sampler, daemon=True)
    t.start()

    mark("start")
    adapter = MotionAdapter.from_pretrained(str(BACKEND / "models" / "animatediff-motion-v3"), torch_dtype=torch.float32)
    mark("motion_adapter")

    sd = StableDiffusionPipeline.from_pretrained(str(BACKEND / "models" / "anime-diffusers"), torch_dtype=torch.float32)
    mark("sd_pipeline")

    pipe = AnimateDiffPipeline.from_pipe(sd, motion_adapter=adapter)
    mark("from_pipe done")

    stop.set()
    t.join()
    print(f"\n峰值 RSS: {peak['rss']:.2f} GB（{peak['at']}）")
    print("可用内存:", f"{psutil.virtual_memory().available / 2**30:.2f} GB")


if __name__ == "__main__":
    main()