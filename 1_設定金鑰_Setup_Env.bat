@echo off
chcp 65001 >nul
title 建立設定檔 [.env]
cd /d "%~dp0"

echo ========================================================
echo   Discord Gemini 機器人 - 設定檔建立小幫手
echo ========================================================
echo.

if exist ".env" goto env_exists
if exist ".env.example" goto create_env
echo [錯誤] 找不到 .env.example 範本檔案。
pause
exit /b 1

:create_env
copy /y ".env.example" ".env" >nul
echo [INFO] 已成功為您建立 .env 設定檔。
goto open_notepad

:env_exists
echo [INFO] .env 設定檔已經存在。

:open_notepad
echo.
echo 即將使用「記事本」打開 .env 設定檔...
echo 請在記事本中填入您的 GEMINI_API_KEY、DISCORD_BOT_TOKEN 與 TARGET_CHANNEL_ID。
echo 填寫完畢後，請按鍵盤 Ctrl + S 儲存，然後關閉記事本。
echo.
start notepad.exe .env
exit /b 0
