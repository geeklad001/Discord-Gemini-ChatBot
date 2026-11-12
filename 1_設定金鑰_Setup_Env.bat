@echo off
title Discord Gemini Bot - Setup .env
cd /d "%~dp0"

echo ========================================================
echo   Discord Gemini Bot - Setup .env
echo ========================================================
echo.

if exist ".env" goto env_exists
if exist ".env.example" goto create_env
echo [ERROR] .env.example not found.
pause
exit /b 1

:create_env
copy /y ".env.example" ".env" >nul
echo [INFO] Created .env configuration file.
goto open_notepad

:env_exists
echo [INFO] .env configuration file already exists.

:open_notepad
echo.
echo Opening .env with Notepad...
echo Please enter your GEMINI_API_KEY, DISCORD_BOT_TOKEN and TARGET_CHANNEL_ID.
echo Press Ctrl + S to save, then close Notepad.
echo.
start notepad.exe .env
exit /b 0
