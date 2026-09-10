@echo off
setlocal
cd /d "%~dp0"
set "DOCKER_CMD=docker"
where docker >nul 2>&1 || (
  echo Docker is not on PATH. Start Docker Desktop and try again.
  pause
  exit /b 1
)

echo ================================================================
echo AI System - LOCAL DASHBOARD TEST
echo ================================================================
echo.
echo This launcher keeps your native Windows Ollama at 127.0.0.1:11434.
echo It rebuilds ONLY the API gateway and starts the dashboard.
echo.

"%DOCKER_CMD%" info >nul 2>&1 || (
  echo Docker Desktop is not ready. Start it and run this again.
  pause
  exit /b 1
)

echo Checking native Ollama on 127.0.0.1:11434...
powershell -NoProfile -Command "$r=try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 http://127.0.0.1:11434/api/tags } catch { $null }; if(-not $r -or $r.StatusCode -ne 200){ exit 1 }"
if errorlevel 1 (
  echo Native Ollama is not responding on port 11434. Start Ollama, then run this again.
  pause
  exit /b 1
)

"%DOCKER_CMD%" compose -f docker-compose.yml -f docker-compose.override.yml build --no-cache api-gateway
if errorlevel 1 goto :fail
REM --no-deps is critical: native Ollama owns port 11434 on this PC.
"%DOCKER_CMD%" compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps api-gateway
if errorlevel 1 goto :fail

echo.
echo Waiting for API gateway...
timeout /t 5 /nobreak >nul
start "" "http://localhost:8000/dashboard"
echo.
echo Dashboard: http://localhost:8000/dashboard
echo Health:    http://localhost:8000/health
echo.
echo If the page is blank or stale, hard refresh with Ctrl+Shift+R.
echo.
pause
exit /b 0
:fail
echo.
echo Build/start failed. Showing gateway logs:
"%DOCKER_CMD%" compose -f docker-compose.yml -f docker-compose.override.yml logs --tail 120 api-gateway
pause
exit /b 1
