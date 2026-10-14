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

HOST = os.environ.get("AIVIDEO_HOST", "127.0.0.1")
PORT = int(os.environ.get("AIVIDEO_PORT", "8000"))

for _d in (MODELS_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
