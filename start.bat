@echo off
chcp 65001 >nul
rem ============================================================
rem  AI 视频工坊 - 启动
rem ============================================================

cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，请先运行 setup.bat 完成安装。
    pause
    exit /b 1
)

echo 正在启动服务...
echo 浏览器将自动打开 http://127.0.0.1:8000
echo 关闭此窗口即可停止服务。
echo.

rem 等服务起来再打开浏览器
start "" /b cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8000"

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo 服务已停止。
pause
