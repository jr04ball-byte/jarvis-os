@echo off
echo ==================================================
echo Self-Hosted AI System - Installation Script
echo ==================================================
echo.
echo System Check:
echo   CPU: Intel i5-11400F
echo   GPU: NVIDIA RTX 3070 (8GB)
echo   RAM: 16GB
echo.

REM Check if Docker is installed
docker --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker is not installed
    echo.
    echo Please install Docker Desktop for Windows:
    echo   1. Download from https://www.docker.com/products/docker-desktop
    echo   2. Install with WSL2 backend enabled
    echo   3. Enable GPU support in Docker settings
    echo   4. Restart this script
    pause
    exit /b 1
)

echo ✅ Docker is installed

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker is not running
    echo Please start Docker Desktop and try again
    pause
    exit /b 1
)

echo ✅ Docker is running
echo.
echo Starting services...
echo.

REM Pull images first
echo 📦 Pulling Docker images (this may take a while)...
docker-compose pull

REM Start services
echo.
echo 🚀 Starting AI system...
docker-compose up -d --build api-gateway

REM Wait for services to be ready
echo.
echo ⏳ Waiting for services to initialize...
timeout /t 15 /nobreak >nul

echo.
echo ==================================================
echo 🎉 AI System Installation Complete!
echo ==================================================
echo.
echo 📥 Next step: Download your first AI model
echo.
echo Recommended starter model (Llama 3.1 8B):
echo   docker exec -it ollama ollama pull llama3.1:8b
echo.
echo This will download ~5GB. Please wait for it to complete.
echo.
echo After downloading, access your AI system at:
echo   🌐 Web Interface: http://localhost:3000
echo   🎨 Image Generation: http://localhost:8188
echo   🔌 API Gateway: http://localhost:8000
echo.
echo Other recommended models:
echo   docker exec -it ollama ollama pull llama3.1:8b
echo   docker exec -it ollama ollama pull mistral:7b
echo   docker exec -it ollama ollama pull deepseek-coder:6.7b
echo.
echo ==================================================
pause

