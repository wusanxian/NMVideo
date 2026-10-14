@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ============================================================
rem  AI 视频工坊 - Windows 一键安装
rem  适用于任意 Windows 10/11（需要 Python 3.11+，Node 可选）
rem ============================================================

cd /d "%~dp0"

echo ============================================
echo   AI 视频工坊 安装程序
echo ============================================
echo.

rem ---- 1. 检查 Python ----
echo [1/6] 检查 Python...
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python。
    echo    请先从 https://www.python.org/downloads/ 安装 Python 3.11 或更高版本，
    echo    安装时务必勾选 "Add Python to PATH"。
    pause
    exit /b 1
)
python --version
echo.

rem ---- 2. 检测显卡，决定 torch 版本 ----
echo [2/6] 检测 NVIDIA 显卡...
set TORCH_MODE=cpu
where nvidia-smi >nul 2>nul
if not errorlevel 1 (
    for /f "skipions=1" %%i in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do (
        echo    检测到: %%i
        set TORCH_MODE=cuda
    )
)
if "%TORCH_MODE%"=="cuda" (
    echo    将安装 CUDA 版 torch ^(需要较新 NVIDIA 驱动^)
) else (
    echo    未检测到 NVIDIA 显卡，将安装 CPU 版 torch
)
echo.

rem ---- 3. 创建虚拟环境并安装依赖 ----
echo [3/6] 创建 Python 虚拟环境...
if not exist "backend\.venv\Scripts\python.exe" (
    python -m venv "backend\.venv"
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败。
        pause
        exit /b 1
    )
)
set VPY=backend\.venv\Scripts\python.exe

echo    升级 pip...
%VPY% -m pip install -U pip -i https://mirrors.aliyun.com/pypi/simple/ --quiet
if errorlevel 1 %VPY% -m pip install -U pip --quiet

echo    安装 torch（约 2-3 GB，耗时较长，请耐心等待）...
if "%TORCH_MODE%"=="cuda" (
    %VPY% -m pip install torch --index-url https://download.pytorch.org/whl/cu124 || %VPY% -m pip install torch --index-url https://download.pytorch.org/whl/cu121 || %VPY% -m pip install torch
) else (
    %VPY% -m pip install torch --index-url https://download.pytorch.org/whl/cpu/ || %VPY% -m pip install torch --index-url https://mirrors.sjtu.edu.cn/pytorch-wheels/cpu/ || %VPY% -m pip install torch --index-url https://mirrors.aliyun.com/pytorch-wheels/cpu/
)
if errorlevel 1 (
    echo [错误] torch 安装失败。请检查网络后重试。
    pause
    exit /b 1
)

echo    安装其余依赖...
%VPY% -m pip install -r backend\requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
if errorlevel 1 (
    echo [错误] 依赖安装失败。
    pause
    exit /b 1
)
echo.

rem ---- 4. 下载模型 ----
echo [4/6] 下载 AI 模型...
echo    请选择要下载的模型：
echo      [1] AnimeDiff 动漫引擎（约 3.6 GB，CPU 可跑，分钟级出片）
echo      [2] Wan 2.2 TI2V-5B（约 32 GB，8GB 显存首选，画质标杆级）
echo      [3] 两者都下（约 36 GB）
set /p MODEL_CHOICE=    输入序号（默认 1）:
if "%MODEL_CHOICE%"=="2" (
    %VPY% backend\scripts\download_models.py --wan-ti2v-5b
) else if "%MODEL_CHOICE%"=="3" (
    %VPY% backend\scripts\download_models.py --all
) else (
    %VPY% backend\scripts\download_models.py
)
if errorlevel 1 (
    echo [警告] 模型下载未全部完成，可稍后重新运行 setup.bat 续传。
)
echo.

rem ---- 5. 构建前端 ----
echo [5/6] 构建前端...
where node >nul 2>nul
if errorlevel 1 (
    echo    [跳过] 未检测到 Node.js。
    echo    后端 API 仍可正常使用；如需网页界面请安装 Node.js 18+
    echo    ^(https://nodejs.org/^) 后重新运行 setup.bat。
) else (
    if exist "frontend\node_modules" (
        echo    前端依赖已存在，跳过安装。
    ) else (
        pushd frontend
        call npm install --registry=https://registry.npmmirror.com --no-audit --no-fund
        popd
    )
    pushd frontend
    call npm run build
    popd
    if errorlevel 1 echo [警告] 前端构建失败，可稍后重试。
)
echo.

rem ---- 6. 完成 ----
echo [6/6] 安装完成！
echo.
echo ============================================
echo   启动方式：双击 start.bat
echo   然后浏览器打开 http://127.0.0.1:8000
echo ============================================
pause
