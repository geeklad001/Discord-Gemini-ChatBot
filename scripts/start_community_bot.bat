@echo off
chcp 65001 >nul
title Discord Gemini Bot [Community]
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found!
    echo Please make sure .venv exists in this folder.
    pause
    exit /b 1
)

if not exist ".env.community" (
    echo [WARNING] .env.community not found!
    echo Please copy .env.community.example to .env.community.
    pause
    exit /b 1
)

if exist "services\tts_server.py" (
    if exist ".venv_tts\Scripts\python.exe" (
        ".venv\Scripts\python.exe" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); rc=s.connect_ex(('127.0.0.1',9880)); s.close(); sys.exit(0 if rc==0 else 1)" >nul 2>&1
        if errorlevel 1 (
            echo [TTS] Starting internal F5-TTS Voice Server in a minimized window...
            start "F5-TTS Lazy-Load Voice Server" /MIN cmd /c "scripts\start_f5_tts_server.bat"
        ) else (
            echo [TTS] Local F5-TTS Voice Server is already running on 127.0.0.1:9880.
        )
    )
)

echo Starting Discord Gemini Bot [Community Profile]...
".venv\Scripts\python.exe" bot.py --profile=community

echo.
echo ========================================================
echo Community Bot process has terminated. Press any key to exit.
echo ========================================================
pause
