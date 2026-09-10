@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\AI-System.exe" (
  start "AI System" "%~dp0dist\AI-System.exe"
  exit /b 0
)
call "%~dp0AI-System.bat"
