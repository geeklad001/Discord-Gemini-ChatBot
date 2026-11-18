@echo off
title Discord Gemini Bot - Setup .env
cd /d "%~dp0.."

echo ========================================================
echo   Discord Gemini Bot - Setup .env
echo ========================================================
echo.

if exist ".env" goto env_exists
if exist ".env.starter.example" goto create_from_starter
if exist ".env.example" goto create_from_default
echo [ERROR] No .env template (.env.starter.example / .env.example) found.
pause
exit /b 1

:create_from_starter
copy /y ".env.starter.example" ".env" >nul
echo [INFO] Created .env from .env.starter.example (Community & TRPG Starter).
goto open_notepad

:create_from_default
copy /y ".env.example" ".env" >nul
echo [INFO] Created .env from .env.example.
goto open_notepad

:env_exists
echo [INFO] .env configuration file already exists.

:open_notepad
echo.
echo Opening .env with Notepad...
echo Please fill in GEMINI_API_KEY, DISCORD_BOT_TOKEN, and TARGET_CHANNEL_ID.
echo Press Ctrl + S to save, then close Notepad.
echo.
start notepad.exe .env
exit /b 0
