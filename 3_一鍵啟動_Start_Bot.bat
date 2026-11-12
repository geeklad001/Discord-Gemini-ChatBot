@echo off
title Discord Gemini Bot
cd /d "%~dp0"

echo ========================================================
echo   Discord Gemini Bot - Starting...
echo ========================================================
echo.

if not exist ".env" goto no_env
if not exist ".venv\Scripts\python.exe" goto auto_install

:start_bot
echo [INFO] Starting bot... Keep this window open.
echo.

".venv\Scripts\python.exe" bot.py

echo.
echo ========================================================
echo   Bot process terminated. Press any key to exit.
echo ========================================================
pause
exit /b 0

:no_env
echo [INFO] .env not found. Creating from .env.example...
if exist ".env.example" copy /y ".env.example" ".env" >nul
echo Please fill in your keys in Notepad, save, and restart.
start notepad.exe .env
pause
exit /b 1

:auto_install
echo [INFO] .venv not found. Running installer first...
for %%f in (*Install*.bat) do call "%%f"
if exist ".venv\Scripts\python.exe" goto start_bot
echo [ERROR] Failed to set up environment.
pause
exit /b 1
