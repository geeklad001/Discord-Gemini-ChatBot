@echo off
chcp 65001 >nul
title Discord Gemini Bot - Optional GPU F5-TTS Installer
cd /d "%~dp0.."

echo ========================================================
echo   Discord Gemini Bot - F5-TTS GPU Environment Installer
echo ========================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

echo [1/4] Checking Python version...
python --version

if not exist ".venv_tts\Scripts\python.exe" (
    echo.
    echo [2/4] Creating isolated virtual environment: .venv_tts
    python -m venv .venv_tts
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv_tts.
        pause
        exit /b 1
    )
) else (
    echo.
    echo [2/4] Virtual environment .venv_tts already exists.
)

echo.
echo [3/4] Installing PyTorch CUDA 12.4 and F5-TTS dependencies...
".venv_tts\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv_tts\Scripts\python.exe" -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install PyTorch CUDA packages.
    pause
    exit /b 1
)

".venv_tts\Scripts\python.exe" -m pip install --extra-index-url https://download.pytorch.org/whl/cu124 torch==2.6.0 torchaudio==2.6.0 -r requirements_tts.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install F5-TTS dependencies.
    pause
    exit /b 1
)

echo.
echo [4/4] Verifying CUDA and imports...
".venv_tts\Scripts\python.exe" -c "import torch, torchaudio, f5_tts, fastapi, uvicorn, soundfile, multipart, dotenv; print('CUDA:', torch.cuda.is_available(), '| Torch:', torch.__version__)"
if %errorlevel% neq 0 (
    echo [ERROR] Verification failed.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   [SUCCESS] F5-TTS environment (.venv_tts) is ready!
echo   start_bot.bat and start_community_bot.bat will now
echo   auto-start the local F5-TTS server when launched.
echo ========================================================
echo.
pause
exit /b 0
