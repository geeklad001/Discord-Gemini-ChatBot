@echo off
title Discord Gemini 機器人 - 一鍵自動安裝
cd /d "%~dp0"

echo ========================================================
echo   Discord Gemini 機器人 - 一鍵自動安裝小幫手
echo ========================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 goto no_python

echo [1/3] 檢查 Python 版本...
python --version

if exist ".venv\Scripts\python.exe" goto venv_exists

echo.
echo [2/3] 正在建立專屬虛擬環境 (.venv)...
python -m venv .venv
if %errorlevel% neq 0 goto venv_fail
echo [完成] 虛擬環境建立成功。
goto install_deps

:venv_exists
echo.
echo [2/3] 專屬虛擬環境 (.venv) 已存在，跳過建立。

:install_deps
echo.
echo [3/3] 正在安裝與更新必要的模組套件...
echo (第一次安裝可能需要 1~3 分鐘，請保持網路連線並稍候)
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if %errorlevel% neq 0 goto install_fail

echo.
echo ========================================================
echo   恭喜！所有環境與套件已安裝成功。
echo.
echo   接下來請確認 .env 已填寫完畢，
echo   然後雙擊執行 [3_一鍵啟動_Start_Bot.bat] 即可讓機器人上線。
echo ========================================================
echo.
pause
exit /b 0

:no_python
echo [錯誤] 找不到 Python。
echo 請先安裝 Python，並務必在安裝的第一個畫面打勾：
echo [v] Add python.exe to PATH
echo.
echo Python 官方下載網址: https://www.python.org/downloads/
echo.
pause
exit /b 1

:venv_fail
echo [錯誤] 建立虛擬環境失敗，請確認資料夾權限或重新安裝 Python。
pause
exit /b 1

:install_fail
echo.
echo [警告] 部分套件安裝可能未成功，請確認網路連線後再次執行本腳本。
pause
exit /b 1
