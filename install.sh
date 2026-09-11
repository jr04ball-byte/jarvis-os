#!/bin/bash

echo "=================================================="
echo "Self-Hosted AI System - Installation Script"
echo "=================================================="
echo ""
echo "System Check:"
echo "  CPU: Intel i5-11400F"
echo "  GPU: NVIDIA RTX 3070 (8GB)"
echo "  RAM: 16GB"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed"
    echo ""
    echo "Please install Docker Desktop for Windows:"
    echo "  1. Download from https://www.docker.com/products/docker-desktop"
    echo "  2. Install with WSL2 backend enabled"
    echo "  3. Enable GPU support in Docker settings"
    echo "  4. Restart this script"
    exit 1
fi

echo "✅ Docker is installed"

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "❌ Docker is not running"
    echo "Please start Docker Desktop and try again"
    exit 1
fi

echo "✅ Docker is running"

# Check for NVIDIA GPU support
if ! docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi &> /dev/null; then
    echo "⚠️  GPU support not detected"
    echo ""
    echo "To enable GPU acceleration:"
    echo "  1. Install NVIDIA Container Toolkit in WSL2"
    echo "  2. Restart Docker Desktop"
    echo "  3. Run this script again"
    echo ""
    echo "Continue without GPU? (will be much slower) [y/N]"
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✅ GPU support enabled"
fi

echo ""
echo "Starting services..."
echo ""

# Pull images first
echo "📦 Pulling Docker images (this may take a while)..."
docker-compose pull

# Start services
echo ""
echo "🚀 Starting AI system..."
docker-compose up -d

# Wait for services to be ready
echo ""
echo "⏳ Waiting for services to initialize..."
sleep 10

# Check service health
echo ""
echo "Checking service health..."

OLLAMA_READY=false
RETRIES=0
MAX_RETRIES=30

while [ $RETRIES -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:11434/api/tags &> /dev/null; then
        OLLAMA_READY=true
        break
    fi
    echo "  Waiting for Ollama... ($RETRIES/$MAX_RETRIES)"
    sleep 2
    RETRIES=$((RETRIES + 1))
done

if [ "$OLLAMA_READY" = true ]; then
    echo "✅ Ollama is ready"
else
    echo "⚠️  Ollama is taking longer than expected"
fi

echo ""
echo "=================================================="
echo "🎉 AI System Installation Complete!"
echo "=================================================="
echo ""
echo "📥 Next step: Download your first AI model"
echo ""
echo "Recommended starter model (Llama 3.1 8B):"
echo "  docker exec -it ollama ollama pull llama3.1:8b"
echo ""
echo "This will download ~5GB. Please wait for it to complete."
echo ""
echo "After downloading, access your AI system at:"
echo "  🌐 Web Interface: http://localhost:3000"
echo "  🎨 Image Generation: http://localhost:8188"
echo "  🔌 API Gateway: http://localhost:8000"
echo ""
echo "Other recommended models:"
echo "  docker exec -it ollama ollama pull llama3.1:8b     # Strong reasoning"
echo "  docker exec -it ollama ollama pull mistral:7b     # Fast all-rounder"
echo "  docker exec -it ollama ollama pull deepseek-coder:6.7b  # Coding"
echo ""
echo "=================================================="

