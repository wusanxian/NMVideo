@echo off
REM ============================================================
REM  AI 动画导演工作台 - Windows 桌面端一键构建
REM  流程：前端 dist -> launcher exe -> Electron NSIS 安装包
REM  产出：desktop/dist/AI动画导演工作台-<版本>-Setup.exe + latest.yml
REM ============================================================
setlocal
cd /d "%~dp0"

REM 宿主是 Electron 应用时（如 IDE 桌面版）会带 ELECTRON_RUN_AS_NODE，
REM 会把 electron.exe 降级成纯 Node，构建/启动前必须清除
set "ELECTRON_RUN_AS_NODE="

REM 构建工具镜像（GitHub 不通时走 npmmirror）
set ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/

set "NODE=node"
set PY=backend\.venv\Scripts\python.exe
set ELEC_VER=37.10.3

echo [0/4] 准备本地 electron 发行 zip（GitHub 不通时从 npmmirror 下载）...
if not exist "desktop\electron-dist\electron-v%ELEC_VER%-win32-x64.zip" (
    if not exist "desktop\electron-dist" mkdir "desktop\electron-dist"
    curl -sL -o "desktop\electron-dist\electron-v%ELEC_VER%-win32-x64.zip" "https://npmmirror.com/mirrors/electron/%ELEC_VER%/electron-v%ELEC_VER%-win32-x64.zip" || goto :err
    echo [OK] electron zip 已就绪
) else (
    echo [OK] electron zip 已存在，跳过下载
)

echo [1/4] 构建前端...
cd frontend
call "%NODE%" node_modules\vite\bin\vite.js build || goto :err
cd ..

echo [2/4] 打包 launcher exe（含前后端载荷）...
"%PY%" packaging\build.py --no-fe || goto :err

echo [3/4] 安装桌面端依赖（首次需要）...
cd desktop
if not exist node_modules (
    call npm install || goto :err
)

echo [4/4] Electron 打包...
REM 本地验证用 --publish never；发布到 GitHub Releases 改成 build-desktop-release.bat
call npx electron-builder --win --publish never || goto :err

REM 安装包命名由 electron-builder.yml 的 artifactName 统一管理。

echo.
echo [OK] 构建完成：desktop\dist\ 下有安装包与 latest.yml
goto :end

:err
echo.
echo [X] 构建失败，请检查上方错误。
exit /b 1

:end
endlocal
