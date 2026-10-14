@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Post-Claim Medical Care Engine - one-click launcher
rem  Bundled Python runtime + all dependencies are included.
rem  No install, no Python needed. Just double-click this file.
rem ============================================================

set "PYTHON=%~dp0runtime\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Bundled runtime not found: runtime\python.exe
    echo         Make sure this folder was fully extracted and that
    echo         the "runtime" subfolder has not been deleted.
    pause
    exit /b 1
)

rem -- Sanity-check that the bundled interpreter actually starts --
"%PYTHON%" --version >NUL 2>NUL
if errorlevel 1 (
    echo [ERROR] The bundled Python runtime failed to start.
    echo         This is usually caused by antivirus software blocking
    echo         python.exe, or by a missing VC++ runtime on this machine.
    echo         Allow python.exe in your antivirus and run this again.
    pause
    exit /b 1
)

rem -- Install dependencies on first run if anything is missing --
"%PYTHON%" -c "import streamlit, rapidocr_onnxruntime, anthropic, pydantic, requests, openpyxl" >NUL 2>NUL
if errorlevel 1 (
    echo [INFO] First run: installing dependencies ^(needs internet, 2-5 min^)...
    "%PYTHON%" -m pip install --no-cache-dir -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. Check your network and retry.
        pause
        exit /b 1
    )
)

rem -- Pre-create the Streamlit config to skip its first-run email prompt --
set "STREAMLIT_DIR=%USERPROFILE%\.streamlit"
if not exist "%STREAMLIT_DIR%\credentials.toml" (
    if not exist "%STREAMLIT_DIR%" mkdir "%STREAMLIT_DIR%"
    echo [general]> "%STREAMLIT_DIR%\credentials.toml"
    echo email = "">> "%STREAMLIT_DIR%\credentials.toml"
)

echo [INFO] Starting the web app, please wait...
echo        Your browser will open http://localhost:8501 automatically.
echo        If it does not open, enter that address manually in the browser.
"%PYTHON%" -m streamlit run app.py

pause
