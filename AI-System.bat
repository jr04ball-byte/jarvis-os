@echo off
setlocal
cd /d "%~dp0"
where pythonw >nul 2>&1
if not errorlevel 1 (
  start "AI System" /min pythonw "%~dp0launcher.py"
  exit /b 0
)
where python >nul 2>&1
if not errorlevel 1 (
  start "AI System" /min python "%~dp0launcher.py"
  exit /b 0
)
echo Python is not installed. Run install.bat first.
pause
