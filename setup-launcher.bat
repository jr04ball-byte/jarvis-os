@echo off
echo ==================================================
echo AI System Launcher - Setup
echo ==================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python is not installed
    echo.
    echo Please install Python 3.8+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo ✅ Python is installed
echo.
echo Installing launcher dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements-launcher.txt

echo.
echo ==================================================
echo ✅ Launcher Setup Complete!
echo ==================================================
echo.
echo To start the launcher:
echo   python launcher.py
echo.
echo Or use the shortcut: AI-System.bat
echo.
pause
