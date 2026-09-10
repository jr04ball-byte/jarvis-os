@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Jarvis - Gemini-first Brain Stack

echo ================================================
echo   JARVIS - GEMINI-FIRST BRAIN STACK
echo   Primary: Gemini   Secondary: OpenCode.ai   Fallback: Ollama
echo ================================================
echo.

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:4096/global/health > $null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo Launching OpenCode brain on :4096 ...
  start "OpenCode-Brain" cmd /k "opencode serve --port 4096 --hostname 127.0.0.1"
  timeout /t 4 /nobreak >nul
) else (
  echo OpenCode brain already online on :4096.
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Jarvis Python environment...
  python -m venv .venv
  if errorlevel 1 goto :fail
)

if exist ".venv\pip.ok" (
  powershell -NoProfile -Command "$a=(Get-Item '.venv\pip.ok').LastWriteTime; $b=(Get-Item 'api-gateway\requirements.txt').LastWriteTime; if($b -gt $a){exit 1}else{exit 0}"
  if errorlevel 1 goto :dopip
  goto :skipip
)
:dopip
call ".venv\Scripts\activate.bat"
python -m pip install --disable-pip-version-check -q -r "api-gateway\requirements.txt"
if errorlevel 1 goto :fail
copy /y "api-gateway\requirements.txt" ".venv\pip.ok" >nul 2>&1
:skipip

if not exist "logs" mkdir logs >nul 2>&1
start "Jarvis-Brain-Gateway" cmd /k "cd /d "%~dp0api-gateway" && ..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"

echo Waiting for the gateway...
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/health; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }"
  if not errorlevel 1 goto :open
  timeout /t 1 /nobreak >nul
)

echo Gateway did not become ready. Check the Jarvis-Brain-Gateway window.
pause
exit /b 1

:open
start "" "http://127.0.0.1:8000/dashboard"
echo.
echo Jarvis is open. Brains: Gemini primary, OpenCode secondary, Ollama fallback.
echo Status: http://127.0.0.1:8000/v1/brain/status
exit /b 0

:fail
echo.
echo Jarvis startup failed. Check the message above.
pause
exit /b 1
