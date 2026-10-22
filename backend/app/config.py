"""全局配置：路径、镜像、服务参数"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent            # backend/
PROJECT_DIR = BASE_DIR.parent                                # aivideo/
MODELS_DIR = Path(os.environ.get("AIVIDEO_MODELS_DIR", BASE_DIR / "models"))
OUTPUTS_DIR = Path(os.environ.get("AIVIDEO_OUTPUTS_DIR", BASE_DIR / "outputs"))
DB_PATH = Path(os.environ.get("AIVIDEO_DB", BASE_DIR / "aivideo.db"))
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"

# huggingface.co 直连不通，默认走 hf-mirror 国内镜像
HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)

# HuggingFace 缓存统一落在模型目录下：MiniMax H3 全套约 139GB（底模 78GB +
# Qwen3-VL 文本编码器 62GB），放默认的 C 盘用户目录会撑爆系统盘。必须在
# import huggingface_hub / diffusers 之前设置，所以放在 config 里（最早导入）。
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf-cache"))
HF_CACHE_DIR = Path(os.environ["HF_HOME"])

HOST = os.environ.get("AIVIDEO_HOST", "127.0.0.1")
PORT = int(os.environ.get("AIVIDEO_PORT", "8000"))

for _d in (MODELS_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
