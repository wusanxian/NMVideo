# -*- coding: utf-8 -*-
# 由 packaging/build.py 自动生成
block_cipher = None

a = Analysis(
    [r"D:\\work\\aivideo\\packaging\\launcher.py"],
    pathex=[],
    binaries=[],
    datas=[(r"D:\\work\\aivideo\\packaging\\payload", 'payload')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'numpy', 'diffusers', 'transformers', 'PIL', 'cv2', 'scipy', 'sympy', 'networkx', 'jinja2', 'markupsafe', 'fsspec', 'huggingface_hub', 'safetensors', 'accelerate', 'fastapi', 'uvicorn', 'pydantic', 'starlette', 'sqlalchemy', 'matplotlib', 'pandas', 'sklearn', 'timm', 'einops', 'sentencepiece', 'torchao', 'torchvision'],
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
    name='AI视频工坊',
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
