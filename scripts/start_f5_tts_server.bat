@echo off
chcp 65001 >nul
title F5-TTS Lazy-Load Voice Server
cd /d "%~dp0.."

if not exist "services\tts_server.py" (
    echo [ERROR] services\tts_server.py not found!
    pause
    exit /b 1
)

set "TTS_PYTHON="
if exist ".venv_tts\Scripts\python.exe" set "TTS_PYTHON=.venv_tts\Scripts\python.exe"
if "%TTS_PYTHON%"=="" if exist ".venv\Scripts\python.exe" set "TTS_PYTHON=.venv\Scripts\python.exe"

if "%TTS_PYTHON%"=="" (
    echo [ERROR] No Python virtual environment found ^(.venv_tts or .venv^)!
    echo Please run scripts\setup_f5_tts.bat first.
    pause
    exit /b 1
)

echo [TTS] Launching F5-TTS Lazy-Load Voice Server using %TTS_PYTHON% ...
"%TTS_PYTHON%" services\tts_server.py
if errorlevel 1 (
    echo.
    echo ========================================================
    echo [ERROR] F5-TTS Server exited with an error.
    echo ========================================================
    pause
    exit /b 1
)
