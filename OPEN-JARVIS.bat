@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================
echo          JARVIS AI SYSTEM - STARTING
echo ================================================
echo.

REM If the API is already running, just open the dashboard.
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8000/health; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 500){exit 0}else{exit 1} } catch { exit 1 }"
if not errorlevel 1 goto :open

REM Make sure Ollama is reachable.
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:11434/api/tags; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 300){exit 0}else{exit 1} } catch { exit 1 }"
if errorlevel 1 (
  echo Ollama is not running or is not reachable.
  echo Start Ollama, then double-click OPEN-JARVIS.bat again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Jarvis Python environment...
  python -m venv .venv
  if errorlevel 1 goto :fail
)

REM Fast path: skip pip when requirements unchanged.
if exist ".venv\pip.ok" (
  powershell -NoProfile -Command "$a=(Get-Item '.venv\pip.ok').LastWriteTime; $b=(Get-Item 'api-gateway\requirements.txt').LastWriteTime; if($b -gt $a){exit 1}else{exit 0}"
  if errorlevel 1 goto :dopip2
  echo Dependencies up to date.
  goto :skipip2
)
:dopip2
call ".venv\Scripts\activate.bat"
python -m pip install --disable-pip-version-check -q -r "api-gateway\requirements.txt"
if errorlevel 1 goto :fail
copy /y "api-gateway\requirements.txt" ".venv\pip.ok" >nul 2>&1
:skipip2

if not exist "logs" mkdir logs >nul 2>&1
start "Jarvis AI API" cmd /k "cd /d "%~dp0api-gateway" && set OLLAMA_URL=http://127.0.0.1:11434 && set OLLAMA_MODEL=auto && ..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"

echo Waiting for Jarvis...
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/health; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }"
  if not errorlevel 1 goto :open
  timeout /t 1 /nobreak >nul
)

echo Jarvis did not become ready. Check the Jarvis AI API window.
pause
exit /b 1

:open
start "" "http://127.0.0.1:8000/dashboard"
echo Jarvis is open.
exit /b 0

:fail
echo.
echo Jarvis startup failed. Check the message above.
pause
exit /b 1
