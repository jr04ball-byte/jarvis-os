@echo off
title Jarvis God's Eye Control Launcher
color 0b
cd /d "%~dp0"

echo =======================================================
echo     LAUNCHING JARVIS GOD'S EYE COMMAND CENTER
echo =======================================================

echo [1/3] Starting OpenCode Go Headless Server (:4096)...
start "OpenCode-Engine" cmd /k "opencode serve --port 4096"

timeout /t 2 >nul

echo [2/3] Starting Windows Host Bridge (:8765)...
start "Host-Bridge" cmd /k "cd host-bridge && "%~dp0.venv\Scripts\python.exe" host_bridge.py"

timeout /t 2 >nul

echo [3/3] Starting FastAPI Main Gateway (:8000)...
start "Jarvis-Gateway" cmd /k "cd api-gateway && "%~dp0.venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 3 >nul

echo Launching God's Eye Tactical Dashboard...
start http://localhost:8000/dashboard.html

echo Systems fully operational.
pause
