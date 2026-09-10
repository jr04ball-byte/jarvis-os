@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Jarvis AI V15 Installer

echo ================================================
echo          JARVIS AI V15 INSTALLER
echo ================================================
echo.
echo Installing this copy as the active desktop Jarvis...
echo.

REM Preserve the known-good configuration from the existing ai-system install if present.
if not exist "%~dp0.env" if exist "%USERPROFILE%\ai-system\.env" (
  copy /y "%USERPROFILE%\ai-system\.env" "%~dp0.env" >nul
  echo [OK] Existing ai-system configuration copied.
)

REM Remove old shortcuts/launchers that could start an older build.
for %%F in ("%USERPROFILE%\Desktop\Jarvis AI.lnk" "%USERPROFILE%\Desktop\AI System.lnk" "%USERPROFILE%\Desktop\Launch AI System.lnk" "%USERPROFILE%\Desktop\Jarvis AI V15.lnk" "%USERPROFILE%\Desktop\Jarvis AI V15.bat") do (
  if exist "%%~F" del /f /q "%%~F" >nul 2>&1
)

REM Use a simple desktop BAT launcher instead of a Windows shortcut COM object.
>"%USERPROFILE%\Desktop\Jarvis AI V15.bat" echo @echo off
>>"%USERPROFILE%\Desktop\Jarvis AI V15.bat" echo cd /d "%~dp0"
>>"%USERPROFILE%\Desktop\Jarvis AI V15.bat" echo call "%~dp0OPEN-JARVIS.bat"

if not exist "%USERPROFILE%\Desktop\Jarvis AI V15.bat" (
  echo.
  echo ERROR: Could not create the desktop launcher.
  pause
  exit /b 1
)

echo [OK] Old Jarvis launchers removed.
echo [OK] Jarvis AI V15 desktop launcher created.
echo.
echo Starting Jarvis V15 now...
echo.
call "%~dp0OPEN-JARVIS.bat"
endlocal
