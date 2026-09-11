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
ollama pull llama3.1:8b
if errorlevel 1 (
  echo.
  echo ERROR: Ollama could not pull llama3.1:8b.
  echo Make sure Ollama is installed and running.
  pause
  exit /b 1
)
echo.
echo Qwen 3.5 9B installed successfully.
echo.
ollama list
 echo.
echo AI System default: llama3.1:8b
 echo You can now run START-LOCAL-TEST.bat
pause

