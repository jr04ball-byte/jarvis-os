@echo off
echo ================================================================
echo ONE-CLICK AI SYSTEM SETUP
echo ================================================================
echo.
echo This script will complete your AI system setup automatically.
echo Run this when you get home from work.
echo.
echo What it does:
echo   1. Checks if Docker Desktop is installed
echo   2. Starts Docker Desktop if needed
echo   3. Waits for Docker to be ready
echo   4. Launches all AI services (Ollama, ComfyUI, WebUI)
echo   5. Downloads Llama 3.1 8B model (~5GB)
echo   6. Opens your AI chat interface
echo.
echo ================================================================
pause
echo.

echo [1/6] Checking Docker installation...
where docker >nul 2>&1
if errorlevel 1 (
    echo.
    echo ❌ Docker Desktop not found. Please install it first:
    echo    Run: Docker Desktop Installer.exe from Downloads folder
    echo    - Check "Use WSL 2"
    echo    - Complete installation
    echo    - Restart if prompted
    echo    - Run this script again
    echo.
    pause
    exit /b 1
)
echo ✅ Docker is installed

echo.
echo [2/6] Starting Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo Waiting for Docker to be ready (this takes 30-60 seconds)...

:wait_for_docker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if errorlevel 1 (
    echo Still waiting for Docker...
    goto wait_for_docker
)
echo ✅ Docker is running

echo.
echo [3/6] Checking NVIDIA GPU support...
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo ⚠️  GPU support not fully configured, but continuing...
    echo    Your RTX 3070 will still work after proper setup
) else (
    echo ✅ GPU support enabled
)

echo.
echo [4/6] Starting AI services...
cd C:\Users\jr04b\ai-system
docker-compose pull
docker-compose up -d --build api-gateway

echo.
echo [5/6] Waiting for services to initialize...
timeout /t 20 /nobreak >nul

echo.
echo [6/6] Downloading AI model (Llama 3.1 8B - ~5GB)...
echo This will take 5-15 minutes depending on your internet speed.
echo.
docker exec -it ollama ollama pull qwen3.5:9b

echo.
echo ================================================================
echo 🎉 SETUP COMPLETE!
echo ================================================================
echo.
echo Your AI system is running at:
echo   🌐 Chat Interface: http://localhost:3000
echo   🎨 Image Generator: http://localhost:8188
echo   🔌 API: http://localhost:8000
echo.
echo Opening chat interface in your browser...
start http://localhost:3000
echo.
echo Additional models you can download:
echo   docker exec -it ollama ollama pull qwen3.5:9b
echo   docker exec -it ollama ollama pull mistral:7b
echo   docker exec -it ollama ollama pull deepseek-coder:6.7b
echo.
echo To stop the system:
echo   docker-compose stop
echo.
echo To start it again later:
echo   docker-compose start
echo.
echo ================================================================
pause
