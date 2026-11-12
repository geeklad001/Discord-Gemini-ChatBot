@echo off
title Discord Gemini Bot - Installer
cd /d "%~dp0"

echo ========================================================
echo   Discord Gemini Bot - Environment Installer
echo ========================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 goto no_python

echo [1/3] Checking Python version...
python --version

if exist ".venv\Scripts\python.exe" goto venv_ok

echo.
echo [2/3] Creating virtual environment: .venv
python -m venv .venv
if %errorlevel% neq 0 goto venv_fail
echo [OK] Virtual environment created.
goto do_install

:venv_ok
echo.
echo [2/3] Virtual environment .venv already exists.

:do_install
echo.
echo [3/3] Installing required packages...
echo Please wait, this may take a few minutes.
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if %errorlevel% neq 0 goto install_fail

echo.
echo ========================================================
echo   [SUCCESS] Installation Complete!
echo   Please make sure .env is configured,
echo   then double-click Step 3 to start the bot!
echo ========================================================
echo.
pause
exit /b 0

:no_python
echo [ERROR] Python is not installed or not in PATH!
echo Please download and install Python from: https://www.python.org/downloads/
echo Remember to check [Add python.exe to PATH] during installation!
echo.
pause
exit /b 1

:venv_fail
echo [ERROR] Failed to create .venv. Please check Python installation.
pause
exit /b 1

:install_fail
echo [ERROR] Package installation failed. Please check internet connection.
pause
exit /b 1
