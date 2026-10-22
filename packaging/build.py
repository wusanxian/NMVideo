# -*- coding: utf-8 -*-
"""
一键构建 Windows 单文件 exe

流程：装 PyInstaller → 组装 payload（backend 源码 + frontend/dist）→ 生成 spec → 构建
产物：packaging/dist/NMVideo.exe（约 9MB，含全部程序文件；
      torch/模型由 exe 首次运行时按硬件自动安装下载）

用法：
    python packaging/build.py            # 完整构建（含前端构建）
    python packaging/build.py --no-fe    # 跳过前端构建（dist 已存在时）
"""
import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PACKAGING = PROJECT / "packaging"
DIST = PACKAGING / "dist"
VERSION = json.loads((PROJECT / "desktop" / "package.json").read_text(encoding="utf-8"))["version"]

# exe 里不需要的大库（运行时由 launcher 装进 venv）
EXCLUDES = [
    "torch", "numpy", "diffusers", "transformers", "PIL", "cv2", "scipy",
    "sympy", "networkx", "jinja2", "markupsafe", "fsspec", "huggingface_hub",
    "safetensors", "accelerate", "fastapi", "uvicorn", "pydantic",
    "starlette", "sqlalchemy", "matplotlib", "pandas", "sklearn",
    "timm", "einops", "sentencepiece", "torchao", "torchvision",
]

# backend 中不打进 payload 的目录/文件
BACKEND_SKIP_DIRS = {"models", "outputs", ".venv", "__pycache__", ".pytest_cache", ".workbuddy", "tests"}
BACKEND_SKIP_FILES = {"aivideo.db", "aivideo.db-wal", "aivideo.db-shm", "settings.json"}


def run(cmd: list, cwd: Path = PROJECT, desc: str = ""):
    if desc:
        print(f"\n=== {desc} ===")
    proc = subprocess.run(cmd, cwd=str(cwd))
    if proc.returncode != 0:
        print(f"\n✘ 命令失败: {' '.join(map(str, cmd))}")
        sys.exit(1)


def build_frontend():
    run(["node", "node_modules/vite/bin/vite.js", "build"], cwd=PROJECT / "frontend",
        desc="构建前端")


def assemble_payload():
    """backend 源码 + frontend/dist → packaging/payload/"""
    payload = PACKAGING / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)

    src_backend = PROJECT / "backend"
    dst_backend = payload / "backend"
    shutil.copytree(
        src_backend, dst_backend,
        ignore=shutil.ignore_patterns(*BACKEND_SKIP_DIRS, *BACKEND_SKIP_FILES),
    )
    # scripts 里只保留 download_models.py（排障脚本已删）
    scripts_dir = dst_backend / "scripts"
    if scripts_dir.exists():
        for f in scripts_dir.iterdir():
            if f.name != "download_models.py":
                f.unlink() if f.is_file() else shutil.rmtree(f)

    dist = PROJECT / "frontend" / "dist"
    if dist.exists():
        shutil.copytree(dist, payload / "frontend" / "dist")
    else:
        print("✘ frontend/dist 不存在，请先构建前端")
        sys.exit(1)

    # 版本标记
    (payload / "VERSION").write_text(f"{VERSION} {date.today().isoformat()}", encoding="utf-8")
    total = sum(f.stat().st_size for f in payload.rglob("*") if f.is_file())
    print(f"✓ payload 组装完成：{total/2**20:.1f} MB")


def write_spec():
    launcher = str(PACKAGING / "launcher.py").replace("\\", "\\\\")
    payload = str(PACKAGING / "payload").replace("\\", "\\\\")
    spec = f'''# -*- coding: utf-8 -*-
# 由 packaging/build.py 自动生成
block_cipher = None

a = Analysis(
    [r"{launcher}"],
    pathex=[],
    binaries=[],
    datas=[(r"{payload}", 'payload')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={EXCLUDES!r},
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NMVideo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
'''
    spec_path = PACKAGING / "launcher.spec"
    spec_path.write_text(spec, encoding="utf-8")
    return spec_path


def main():
    parser = argparse.ArgumentParser(description="构建 Windows 单文件 exe")
    parser.add_argument("--no-fe", action="store_true", help="跳过前端构建")
    args = parser.parse_args()

    venv_py = PROJECT / "backend" / ".venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        print("✘ 未找到 backend/.venv，请先创建虚拟环境")
        sys.exit(1)

    # 1. 装 PyInstaller
    if importlib.util.find_spec("PyInstaller") is None:
        run([str(venv_py), "-m", "pip", "install", "pyinstaller", "-q",
             "-i", "https://mirrors.aliyun.com/pypi/simple/"], desc="安装 PyInstaller")

    # 2. 确认前端产物存在
    if not args.no_fe:
        build_frontend()
    fe_dist = PROJECT / "frontend" / "dist" / "index.html"
    if not fe_dist.exists():
        print("✘ 未找到 frontend/dist，请先在 frontend 目录执行 npm run build")
        sys.exit(1)

    # 3. 组装 payload + spec
    assemble_payload()
    spec_path = write_spec()

    # 4. 构建
    pyi_dist = PACKAGING / ".build-dist"
    pyi_build = PACKAGING / ".build-work"
    run([str(venv_py), "-m", "PyInstaller", "--clean", "--noconfirm",
         "--distpath", str(pyi_dist), "--workpath", str(pyi_build), str(spec_path)],
        desc="PyInstaller 构建（约 1-2 分钟）")

    # 5. 收尾：重命名带版本号，清理中间产物
    # 注意：PyInstaller 的 dist/build 目录相对当前工作目录（项目根）
    built = pyi_dist / "NMVideo.exe"
    if not built.exists():
        print(f"✘ 未找到构建产物（{built}）")
        sys.exit(1)
    DIST.mkdir(exist_ok=True)
    final = DIST / "NMVideo.exe"
    shutil.move(str(built), str(final))
    shutil.rmtree(pyi_build, ignore_errors=True)
    shutil.rmtree(pyi_dist, ignore_errors=True)

    size_mb = final.stat().st_size / 2**20
    print(f"""
╔════════════════════════════════════════════════════════╗
║  构建完成！                                            ║
║                                                        ║
║  产物: packaging/dist/{final.name:<33}║
║  大小: {size_mb:6.1f} MB                                       ║
║                                                        ║
║  使用方法：拷贝到任意 Windows 电脑双击运行。            ║
║  首次运行会自动：探测硬件 → 装 Python 环境 →            ║
║  按显卡装 torch → 下载匹配模型 → 启动服务。             ║
╚════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
