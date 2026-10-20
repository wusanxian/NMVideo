"""释放未使用的本地模型，避免切换引擎后同时占用显存。"""
import gc
import sys


def unload_pipeline(engine) -> None:
    with engine._load_lock:
        engine._pipe = None
        gc.collect()
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
