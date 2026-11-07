@echo off
title Discord Gemini Bot [社群專案版]
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found!
    echo Please make sure .venv exists in this folder.
    pause
    exit /b 1
)

if not exist ".env.community" (
    echo ========================================================
    echo [WARNING] 尚未找到 .env.community 設定檔！
    echo 請先將 .env.community.example 複製為 .env.community，
    echo 並填入第二個帳號的 GEMINI_API_KEY 與 DISCORD_BOT_TOKEN。
    echo ========================================================
    pause
    exit /b 1
)

echo Starting Discord Gemini Bot [Community Profile]...
".venv\Scripts\python.exe" bot.py --profile=community

echo.
echo ========================================================
echo Community Bot process has terminated. Press any key to exit.
echo ========================================================
pause
