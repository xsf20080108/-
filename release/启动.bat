@echo off
chcp 65001 >NUL
setlocal
cd /d "%~dp0"

echo ==============================================
echo  理赔医护关怀引擎 - 一键启动
echo ==============================================
echo.

set PY=runtime\python.exe

if not exist "%PY%" (
    echo [错误] 未找到内置 Python: %CD%\runtime\python.exe
    echo 请确认已完整解压 zip 后再运行本脚本。
    pause
    exit /b 1
)

echo [1/2] 正在启动服务，请稍候约 10 秒...
start "" http://localhost:8501

echo [2/2] 服务窗口即将打开，请勿关闭本窗口。
echo       浏览器如未自动打开，请手动访问: http://localhost:8501
echo.
"%PY%" -m streamlit run app.py --server.headless true --server.port 8501

echo.
echo 服务已停止。按任意键关闭窗口...
pause >NUL
