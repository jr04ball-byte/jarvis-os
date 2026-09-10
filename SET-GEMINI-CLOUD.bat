@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AI System - Google Gemini Cloud Setup

echo ================================================
echo AI System - Google Gemini Cloud Setup
echo ================================================
echo.
echo This connects Gemini to your Google Cloud project.
echo Your API key stays server-side in the local .env file.
echo.
set /p "GEMINI_KEY=Paste your Gemini API key (input is hidden only by normal CMD limitations): "
if "%GEMINI_KEY%"=="" (
  echo No key entered. Nothing changed.
  pause
  exit /b 1
)
set /p "PROJECT_ID=Optional Google Cloud project ID (press Enter to skip): "
set "GEMINI_KEY=%GEMINI_KEY%"
set "PROJECT_ID=%PROJECT_ID%"

if exist .env (copy /y .env .env.backup-gemini >nul)
if not exist .env copy /y .env.example .env >nul
powershell -NoProfile -Command "$p='.env'; $s=Get-Content -Raw $p; if($s -notmatch '(?m)^GEMINI_API_KEY='){ $s += \"`r`nGEMINI_API_KEY=`r`n\" }; $s=[regex]::Replace($s,'(?m)^GEMINI_API_KEY=.*$','GEMINI_API_KEY='+$env:GEMINI_KEY); if($env:PROJECT_ID){ $s=[regex]::Replace($s,'(?m)^GOOGLE_CLOUD_PROJECT=.*$','GOOGLE_CLOUD_PROJECT='+$env:PROJECT_ID) }; Set-Content -NoNewline -Encoding UTF8 $p $s" 
if errorlevel 1 (
  echo Failed to update .env.
  pause
  exit /b 1
)

echo.
echo Gemini configuration saved.
echo Test it after starting AI System with:
echo   http://127.0.0.1:8000/v1/gemini/status
echo.
echo IMPORTANT: never paste your API key into chat or source files.
pause
