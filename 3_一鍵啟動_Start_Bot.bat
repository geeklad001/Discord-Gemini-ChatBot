@echo off
chcp 65001 >nul
title Discord Gemini 聊天機器人
cd /d "%~dp0"

echo ========================================================
echo   Discord Gemini 聊天機器人 - 啟動中
echo ========================================================
echo.

if not exist ".env" goto no_env
if not exist ".venv\Scripts\python.exe" goto auto_install

:start_bot
echo [INFO] 正在啟動機器人...
echo [TIP] 保持此視窗開啟即可維持機器人在線；關閉視窗即可讓機器人下線。
echo.

".venv\Scripts\python.exe" bot.py

echo.
echo ========================================================
echo   機器人已關閉或終止。請按任意鍵離開視窗。
echo ========================================================
pause
exit /b 0

:no_env
echo [提示] 尚未建立 .env 設定檔，正在自動為您建立...
if exist ".env.example" copy /y ".env.example" ".env" >nul
echo 請在剛剛打開的記事本中填入您的金鑰與 Token，存檔後重新執行本腳本。
start notepad.exe .env
pause
exit /b 1

:auto_install
echo [提示] 尚未安裝執行環境 (.venv)，正在自動為您執行安裝...
echo.
call "2_一鍵安裝_Install.bat"
if exist ".venv\Scripts\python.exe" goto start_bot
echo.
echo [錯誤] 自動安裝未完成，請手動確認網路連線與 Python 安裝。
pause
exit /b 1
