@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================================
echo AI SYSTEM v13 - JARVIS LOCAL TEST MODE
echo ================================================================
echo.

echo [1/5] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found on PATH.
  echo Install Python 3.11+ and check "Add Python to PATH".
  pause
  exit /b 1
)
python --version

echo.
echo [2/5] Checking Ollama...
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:11434/api/tags; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 300){exit 0}else{exit 1} } catch { exit 1 }"
if errorlevel 1 (
  echo Ollama is not responding at 127.0.0.1:11434.
  echo Start Ollama on Windows, then run this test again.
  pause
  exit /b 1
)
echo Ollama is ONLINE.

echo.
echo [3/5] Preparing local Python environment (fast path)...
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto :fail
)
REM Skip pip install when requirements haven't changed since last good install.
if exist ".venv\pip.ok" (
  powershell -NoProfile -Command "$a=(Get-Item '.venv\pip.ok').LastWriteTime; $b=(Get-Item 'api-gateway\requirements.txt').LastWriteTime; if($b -gt $a){exit 1}else{exit 0}"
  if errorlevel 1 goto :dopip
  echo Dependencies up to date, skipping pip install.
  goto :skipip
)
:dopip
call ".venv\Scripts\activate.bat"
python -m pip install --disable-pip-version-check -q -r "api-gateway\requirements.txt"
if errorlevel 1 goto :fail
copy /y "api-gateway\requirements.txt" ".venv\pip.ok" >nul 2>&1
:skipip

echo.
echo [4/5] Starting API Gateway locally (no Docker)...
if not exist "logs" mkdir logs >nul 2>&1
REM Free port 8000 only if held by Python/uvicorn from a prior run.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { $p=Get-Process -Id $_ -ErrorAction SilentlyContinue; if($p -and ($p.ProcessName -like '*python*' -or $p.CommandLine -like '*uvicorn*')){ Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } }" >nul 2>&1
start "AI System API" cmd /k "cd /d "%~dp0api-gateway" && set OLLAMA_URL=http://127.0.0.1:11434 && set OLLAMA_MODEL=auto && ..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"

echo Checking default model llama3.1:8b...
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:11434/api/show -Method POST -ContentType 'application/json' -Body '{\"name\":\"llama3.1:8b\"}'; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 echo WARNING: llama3.1:8b not found in Ollama. Run: ollama pull llama3.1:8b

echo Waiting for API...
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8000/health; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }"
  if not errorlevel 1 goto :ready
  timeout /t 1 /nobreak >nul
)
echo API did not become ready. Check the API window for the error.
pause
exit /b 1

:ready
echo.
echo [5/5] Opening AI System...
start "" "http://127.0.0.1:8000/dashboard"
echo.
echo ================================================================
echo AI SYSTEM IS READY FOR TESTING
 echo ================================================================
echo Dashboard: http://127.0.0.1:8000/dashboard
echo Voice:     http://127.0.0.1:8000/voice
 echo API:       http://127.0.0.1:8000/docs
echo.
echo IMPORTANT: Keep the "AI System API" window open while testing.
echo Close that window to stop the local API.
echo ================================================================
pause
exit /b 0

:fail
echo.
echo SETUP FAILED. See the message above.
pause
exit /b 1

