@echo off
setlocal
cd /d "%~dp0"
echo Checking Jarvis...
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://localhost:8000/health; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo Jarvis is NOT running.
  echo Double-click OPEN-JARVIS.bat first, wait for the dashboard, then run this again.
  pause
  exit /b 1
)
echo Jarvis is running. Opening Google sign-in...
powershell -NoProfile -Command "$j=Invoke-RestMethod -UseBasicParsing http://localhost:8000/auth/google/start; Start-Process $j.url; echo $j.url"
echo.
echo A Google sign-in page just opened in your browser.
echo 1. Pick your Gmail account
echo 2. Click Allow
echo 3. You will land on a page saying ok:true with your email - that means done.
echo.
pause
