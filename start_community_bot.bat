@echo off
chcp 65001 >nul
title Discord Gemini Bot [Community]
cd /d "%~dp0"

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

echo Starting Discord Gemini Bot [Community Profile]...
".venv\Scripts\python.exe" bot.py --profile=community

echo.
echo ========================================================
echo Community Bot process has terminated. Press any key to exit.
echo ========================================================
pause
