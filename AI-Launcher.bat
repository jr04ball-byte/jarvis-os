@echo off
title AI System Launcher
color 0A

:menu
cls
echo ================================================
echo           AI SYSTEM LAUNCHER
echo ================================================
echo.
echo Status Check...
docker-compose ps --format "table {{.Name}}\t{{.Status}}" 2>nul
echo.
echo ================================================
echo.
echo [1] Start AI System
echo [2] Stop AI System
echo [3] Open Chat Interface (Web)
echo [4] Open API Documentation
echo [5] Download New Models
echo [6] View System Logs
echo [7] Check GPU Status
echo [8] Exit
echo.
echo ================================================
set /p choice=Enter your choice (1-8):

if "%choice%"=="1" goto start
if "%choice%"=="2" goto stop
if "%choice%"=="3" goto chat
if "%choice%"=="4" goto api
if "%choice%"=="5" goto models
if "%choice%"=="6" goto logs
if "%choice%"=="7" goto gpu
if "%choice%"=="8" goto exit
goto menu

:start
cls
echo Starting AI System...
docker-compose up -d --build api-gateway
echo.
echo ✅ AI System started!
echo.
pause
goto menu

:stop
cls
echo Stopping AI System...
docker-compose stop
echo.
echo ✅ AI System stopped!
echo.
pause
goto menu

:chat
start http://localhost:3000
goto menu

:api
start http://localhost:8000/docs
goto menu

:models
cls
echo ================================================
echo        DOWNLOAD AI MODELS
echo ================================================
echo.
echo Available Models:
echo [1] Llama 3.1 8B (5GB) - Fast and capable
echo [2] Qwen 2.5 7B (5GB) - Strong reasoning
echo [3] Mistral 7B (5GB) - Good all-rounder
echo [4] DeepSeek Coder 6.7B (4GB) - Coding specialist
echo [5] Back to main menu
echo.
set /p model=Enter choice (1-5):

if "%model%"=="1" docker exec ollama ollama pull llama3.1:8b
if "%model%"=="2" docker exec ollama ollama pull llama3.1:8b
if "%model%"=="3" docker exec ollama ollama pull mistral:7b
if "%model%"=="4" docker exec ollama ollama pull deepseek-coder:6.7b
if "%model%"=="5" goto menu

if not "%model%"=="5" (
    echo.
    echo ✅ Model downloaded!
    pause
)
goto menu

:logs
cls
echo Viewing logs... (Press Ctrl+C to stop)
echo.
docker-compose logs -f --tail=50
pause
goto menu

:gpu
cls
echo GPU Status:
echo.
nvidia-smi
echo.
pause
goto menu

:exit
echo.
echo Goodbye!
timeout /t 2 /nobreak >nul
exit

