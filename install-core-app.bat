@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================================
echo AI SYSTEM v9 - WINDOWS CORE APPLICATION INSTALLER
echo ================================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found.
  echo Install Python 3.11+ with Add Python to PATH enabled, then run this again.
  pause
  exit /b 1
)

python -m pip install -r requirements-launcher.txt
if errorlevel 1 (
  echo Failed to install launcher dependencies.
  pause
  exit /b 1
)

python -m pip install pyinstaller
if errorlevel 1 (
  echo Failed to install PyInstaller.
  pause
  exit /b 1
)

echo.
echo Building AI-System.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name AI-System launcher.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

if not exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\AI System" mkdir "%APPDATA%\Microsoft\Windows\Start Menu\Programs\AI System"
copy /Y "%~dp0dist\AI-System.exe" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\AI System\AI System.exe" >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\AI System.lnk'); $s.TargetPath=(Resolve-Path 'dist\AI-System.exe').Path; $s.WorkingDirectory=(Get-Location).Path; $s.Description='AI System - Local AI Control Center'; $s.Save()"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$startup=[Environment]::GetFolderPath('Startup'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup 'AI System.lnk')); $s.TargetPath=(Resolve-Path 'dist\AI-System.exe').Path; $s.WorkingDirectory=(Get-Location).Path; $s.Description='Start AI System'; $s.Save()"

if not exist "%~dp0launcher-config.json" (
  >"%~dp0launcher-config.json" echo {"auto_start":true,"open_dashboard":true}
)

echo.
echo ================================================================
echo AI SYSTEM CORE APPLICATION INSTALLED
echo ================================================================
echo.
echo Desktop shortcut: AI System

echo Windows startup: enabled

echo System tray: enabled

echo Dashboard: http://127.0.0.1:8000/dashboard

echo.
echo Double-click AI System on the desktop to launch it now.
echo.
pause
