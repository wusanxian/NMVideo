@echo off
REM ============================================================
REM  发布版构建：打出安装包并上传 GitHub Releases
REM  前置条件：
REM   1. 本机 git 能推送 GitHub（HTTPS + Credential Manager 已缓存）
REM   2. desktop/package.json 的 version 已按语义化版本提升
REM   3. GH_TOKEN 环境变量已设置（electron-builder 发布用）
REM      set GH_TOKEN=ghp_xxx   （GitHub Settings - Developer settings - Tokens）
REM ============================================================
setlocal
cd /d %~dp0..

REM 宿主是 Electron 应用时（如 IDE 桌面版）会带 ELECTRON_RUN_AS_NODE，需清除
set "ELECTRON_RUN_AS_NODE="

REM 构建工具镜像（GitHub 不通时走 npmmirror）
set ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/

set NODE=C:\Users\23350\.workbuddy\binaries\node\versions\22.22.2-6\node.exe
set PY=backend\.venv\Scripts\python.exe
set ELEC_VER=37.10.3

echo [0/5] 准备本地 electron 发行 zip...
if not exist "desktop\electron-dist\electron-v%ELEC_VER%-win32-x64.zip" (
    if not exist "desktop\electron-dist" mkdir "desktop\electron-dist"
    curl -sL -o "desktop\electron-dist\electron-v%ELEC_VER%-win32-x64.zip" "https://npmmirror.com/mirrors/electron/%ELEC_VER%/electron-v%ELEC_VER%-win32-x64.zip" || goto :err
    echo [OK] electron zip 已就绪
) else (
    echo [OK] electron zip 已存在，跳过下载
)

echo [1/5] 构建前端...
cd frontend
call "%NODE%" node_modules\vite\bin\vite.js build || goto :err
cd ..

echo [2/5] 打包 launcher exe...
"%PY%" packaging\build.py --no-fe || goto :err

echo [3/5] 安装桌面端依赖（首次需要）...
cd desktop
if not exist node_modules (
    call npm install || goto :err
)

echo [4/5] Electron 打包并发布到 GitHub Releases...
call npx electron-builder --win --publish always || goto :err

REM electron-builder 的 latest.yml 用 npm name 拼安装包名，实际文件名却用 productName，
REM 两者不一致会导致自动更新找不到文件——统一重命名为 latest.yml 内的名字再上传
for /f "tokens=2 delims= " %%p in ('findstr /b "path:" desktop\dist\latest.yml') do set UPDATENAME=%%p
if defined UPDATENAME (
    for %%f in ("desktop\dist\* Setup *.exe") do move /y "%%f" "desktop\dist\%UPDATENAME%" >nul
    for %%f in ("desktop\dist\* Setup *.exe.blockmap") do move /y "%%f" "desktop\dist\%UPDATENAME%.blockmap" >nul
    echo [OK] 安装包已重命名为 %UPDATENAME%
)

echo.
echo [OK] 已发布。用户在旧版本里点「检查更新」即可自动升级。
goto :end

:err
echo.
echo [X] 发布失败，请检查上方错误（常见：GH_TOKEN 未设置/无权限）。
exit /b 1

:end
endlocal
