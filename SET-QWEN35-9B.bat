@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo AI System - Qwen 3.5 9B Setup
echo ================================================
echo.
echo Pulling official Ollama Qwen 3.5 9B model...
echo This is approximately 6.6 GB.
echo.
ollama pull qwen3.5:9b
if errorlevel 1 (
  echo.
  echo ERROR: Ollama could not pull qwen3.5:9b.
  echo Make sure Ollama is installed and running.
  pause
  exit /b 1
)
echo.
echo Qwen 3.5 9B installed successfully.
echo.
ollama list
 echo.
echo AI System default: qwen3.5:9b
 echo You can now run START-LOCAL-TEST.bat
pause
