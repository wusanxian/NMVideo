"""引擎级冒烟测试：最小参数跑通 加载→推理→存帧 全链路。"""
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.engines.anime import AnimeEngine  # noqa: E402


def main():
    engine = AnimeEngine()
    out = BACKEND / "outputs" / "_smoke_frames"
    t0 = time.time()

    def on_progress(frac, stage):
        print(f"  [{frac*100:5.1f}%] {stage}", flush=True)

    paths = engine.generate(
        prompt="masterpiece, best quality, anime style, 1girl, silver hair, cherry blossom garden",
        negative_prompt="worst quality, low quality, blurry",
        width=256,
        height=256,
        num_frames=8,
        fps=8,
        steps=12,
        guidance=7.5,
        seed=42,
        frames_dir=out,
        on_progress=on_progress,
        is_cancelled=lambda: False,
    )
    print(f"\n✅ 生成 {len(paths)} 帧，耗时 {time.time()-t0:.1f}s")
    print(f"   首帧: {paths[0]}")
    print(f"   大小: {paths[0].stat().st_size/1024:.0f} KB")


if __name__ == "__main__":
    main()