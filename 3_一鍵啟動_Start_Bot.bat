@echo off
chcp 65001 >nul
title Discord Gemini 聊天機器人
cd /d "%~dp0"

echo ========================================================
echo   Discord Gemini 聊天機器人 - 啟動中
echo ========================================================
echo.

if not exist ".env" (
    echo [錯誤] 找不到 .env 設定檔！
    echo 請先雙擊執行 [1_設定金鑰_Setup_Env.bat] 並填入您的金鑰與 Token！
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [錯誤] 尚未安裝執行環境 (.venv)！
    echo 請先雙擊執行 [2_一鍵安裝_Install.bat] 完成安裝！
    echo.
    pause
    exit /b 1
)

echo [INFO] 正在啟動機器人...
echo [TIP] 保持此視窗開啟即可維持機器人在線；關閉視窗即可讓機器人下線。
echo.

".venv\Scripts\python.exe" bot.py

echo.
echo ========================================================
echo   機器人已關閉或終止。請按任意鍵關閉此視窗。
echo ========================================================
pause
