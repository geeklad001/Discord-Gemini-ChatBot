@echo off
title Discord Gemini Bot
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found!
    echo Please make sure .venv exists in this folder.
    pause
    exit /b 1
)

echo Starting Discord Gemini Bot...
".venv\Scripts\python.exe" bot.py

echo.
echo ========================================================
echo Bot process has terminated. Press any key to exit.
echo ========================================================
pause
