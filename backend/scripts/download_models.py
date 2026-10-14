"""模型下载脚本：全部引擎所需模型（走 hf-mirror 镜像）

用法：
    python scripts/download_models.py                  # 下载 AnimeDiff 模型
    python scripts/download_models.py --wan-ti2v-5b    # 下载 Wan 2.2 TI2V-5B（约 32GB，8GB 显存首选）
    python scripts/download_models.py --wan-a14b       # 下载 Wan 2.2 T2V-A14B（约 119GB，需 60GB+ 内存）
    python scripts/download_models.py --all            # 全部下载（磁盘杀手，按需）

可靠性设计（踩坑记录）：
  - hf-mirror 的 Xet 存储会让 snapshot_download 静默丢文件（dreamlike-anime 曾只下到
    text_encoder，unet/vae 缺失但不报错）。因此每次下载后都做「文件清单 + 大小」校验，
    不完整的文件用 urllib 直连 resolve 链接补下。
"""
import argparse
import fnmatch
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 加入 backend/ 到路径

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from app.config import MODELS_DIR  # noqa: E402

try:
    from huggingface_hub import snapshot_download  # noqa: E402
except ImportError:
    snapshot_download = None

# 动漫底模：diffusers 格式（from_pretrained 本地加载，零网络依赖）
# 不用单文件 ckpt：from_single_file 需要从 hub 拉管线配置，且大文件 safetensors 加载会触发原生段错误
ANIME_REPO = "dreamlike-art/dreamlike-anime-1.0"
# guoyww 的 v3-3 仓库在 HF 上已不可用；v1-5-3 即 SD1.5 的 V3 运动模块（mm_sd_v15_v3），diffusers 格式
MOTION_REPO = "guoyww/animatediff-motion-adapter-v1-5-3"

# Wan 2.2：8GB 显存 + 32GB 内存的甜点模型（bf16 权重约 10GB）
WAN_TI2V_5B_REPO = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
# Wan 2.2 画质标杆：MoE 双专家，需要 60GB+ 内存 + 20GB+ 显存（bf16 约 56GB）
WAN_T2V_A14B_REPO = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"

# Wan 仓库排除项：示例视频/flower 演示图/旧版 ckpt 转换残留
WAN_IGNORE = ["*.mp4", "*.gif", "*.png", "*.jpg", "*.jpeg", "prompt*.json", "*.msgpack"]

HF_ENDPOINT = os.environ["HF_ENDPOINT"]


# ---------- HF API：仓库文件清单 ----------
def api_files(repo: str, retries: int = 3) -> dict:
    """返回 {rfilename: size}"""
    url = f"{HF_ENDPOINT}/api/models/{repo}?blobs=true"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aivideo-downloader"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            return {s["rfilename"]: s.get("size", 0) or 0 for s in data.get("siblings", [])}
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [警告] 无法获取 {repo} 文件清单: {e}")
                return {}
            time.sleep(2)
    return {}


# ---------- 直连下载（snapshot_download 的兜底） ----------
def direct_download(repo: str, files: dict, local_dir: Path,
                    allow=None, ignore=None):
    """urllib 直连 hf-mirror resolve 链接下载/补齐文件"""
    todo = []
    for fname, size in files.items():
        if allow and not any(fnmatch.fnmatch(fname, p) for p in allow):
            continue
        if ignore and any(fnmatch.fnmatch(fname, p) for p in ignore):
            continue
        dest = local_dir / fname
        if dest.exists() and (not size or dest.stat().st_size == size):
            continue
        todo.append((fname, size, dest))

    if not todo:
        return True

    total_bytes = sum(s for _, s, _ in todo if s)
    done_bytes = 0
    for fname, size, dest in todo:
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{HF_ENDPOINT}/{repo}/resolve/main/{fname}"
        tmp = dest.with_suffix(dest.suffix + ".part")
        print(f"  ↓ {fname}（{size/2**30:.2f} GB）")
        last_err = None
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "aivideo-downloader"})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                if size and tmp.stat().st_size != size:
                    raise IOError(f"大小不符: {tmp.stat().st_size} != {size}")
                tmp.replace(dest)
                last_err = None
                break
            except Exception as e:
                last_err = e
                wait = min(2 ** attempt, 15)
                print(f"    重试（{e}），{wait}s 后第 {attempt+2} 次")
                time.sleep(wait)
        if last_err is not None:
            print(f"  [失败] {fname}: {last_err}")
            tmp.unlink(missing_ok=True)
            return False
        done_bytes += size
        pct = done_bytes / total_bytes * 100 if total_bytes else 100
        print(f"    完成（总进度 {pct:.0f}%）")
    return True


# ---------- 校验 ----------
def is_complete(local_dir: Path, files: dict, allow=None, ignore=None) -> bool:
    """本地文件是否覆盖了远端清单（按 allow/ignore 过滤后大小一致）"""
    if not files:
        return bool(local_dir.exists() and any(local_dir.iterdir()))
    for fname, size in files.items():
        if allow and not any(fnmatch.fnmatch(fname, p) for p in allow):
            continue
        if ignore and any(fnmatch.fnmatch(fname, p) for p in ignore):
            continue
        if not size:
            continue
        dest = local_dir / fname
        if not dest.exists() or dest.stat().st_size != size:
            return False
    return True


def download(repo: str, local_dir: Path, allow=None, ignore=None):
    files = api_files(repo)
    if not files:
        # 拿不到清单时退回 snapshot_download 的原子行为
        files = {}

    if is_complete(local_dir, files, allow, ignore):
        size_gb = sum(f.stat().st_size for f in local_dir.rglob("*") if f.is_file()) / 1e9
        print(f"[OK] {repo} 已存在且完整（{local_dir}，{size_gb:.2f} GB），跳过。删除该目录可强制重下。")
        return

    print(f"→ 下载 {repo} → {local_dir}（大文件，耗时较长，支持断点续传）")

    # 第一遍：snapshot_download（多线程 + tqdm 进度）
    if snapshot_download is not None:
        try:
            snapshot_download(
                repo_id=repo,
                local_dir=str(local_dir),
                allow_patterns=list(allow) if allow else None,
                ignore_patterns=list(ignore) if ignore else None,
                max_workers=4,
            )
        except Exception as e:
            print(f"  [警告] snapshot_download 失败（{e}），改用直连下载")

    # 第二遍：校验 + 直连补齐（治 Xet 静默丢文件）
    if files and not is_complete(local_dir, files, allow, ignore):
        missing = [
            f for f, s in files.items()
            if s and (not (local_dir / f).exists() or (local_dir / f).stat().st_size != s)
        ]
        print(f"  [校验] {len(missing)} 个文件缺失/不完整，直连补齐…")
        ok = direct_download(repo, files, local_dir, allow=allow, ignore=ignore)
        if not ok:
            print(f"  [失败] {repo} 未能完整下载，重新运行本脚本可续传")
            sys.exit(1)

    size_gb = sum(f.stat().st_size for f in local_dir.rglob("*") if f.is_file()) / 1e9
    print(f"[OK] {repo} 下载完成（{size_gb:.2f} GB）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="下载引擎模型")
    parser.add_argument("--skip-anime", action="store_true", help="跳过动漫底模")
    parser.add_argument("--skip-motion", action="store_true", help="跳过运动模块")
    parser.add_argument("--wan-ti2v-5b", action="store_true", help="下载 Wan 2.2 TI2V-5B（约 32GB）")
    parser.add_argument("--wan-a14b", action="store_true", help="下载 Wan 2.2 T2V-A14B（约 119GB）")
    parser.add_argument("--all", action="store_true", help="下载全部模型")
    args = parser.parse_args()

    want_wan = args.wan_ti2v_5b or args.wan_a14b or args.all
    want_anime = not (args.wan_ti2v_5b or args.wan_a14b)  # 只下 Wan 时跳过 AnimeDiff

    print(f"HF 镜像: {HF_ENDPOINT}")
    print(f"模型目录: {MODELS_DIR}\n")

    if want_anime:
        if not args.skip_motion:
            # CPU 推理用 fp32 权重，只下 diffusion_pytorch_model.safetensors（跳过 fp16 版省 1.5G）
            download(
                MOTION_REPO,
                MODELS_DIR / "animatediff-motion-v3",
                allow=("config.json", "diffusion_pytorch_model.safetensors"),
            )
        if not args.skip_anime:
            # diffusers 格式仓库：config + tokenizer + safetensors 权重
            download(
                ANIME_REPO,
                MODELS_DIR / "anime-diffusers",
                allow=("*.json", "*.txt", "*.safetensors", "*.model"),
            )

    if want_wan:
        if args.wan_ti2v_5b or args.all:
            download(WAN_TI2V_5B_REPO, MODELS_DIR / "wan-ti2v-5b", ignore=WAN_IGNORE)
        if args.wan_a14b or args.all:
            download(WAN_T2V_A14B_REPO, MODELS_DIR / "wan-t2v-a14b", ignore=WAN_IGNORE)

    print("\n全部完成！现在可以启动后端生成视频了。")
