@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo  JARVIS V23 - Blender Dashboard Bake
echo ========================================

set "BLENDER_EXE="
for /f "delims=" %%I in ('where blender.exe 2^>nul') do if not defined BLENDER_EXE set "BLENDER_EXE=%%I"

if not defined BLENDER_EXE if exist "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
if not defined BLENDER_EXE if exist "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
if not defined BLENDER_EXE if exist "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
if not defined BLENDER_EXE if exist "C:\Program Files\Blender Foundation\Blender 4.3\blender.exe" set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 4.3\blender.exe"

if not defined BLENDER_EXE (
  echo [ERROR] Blender was not found.
  echo Install Blender, then run this file again. The dashboard still works now using its existing Blender orb and live CSS motion fallback.
  exit /b 2
)

echo Blender: %BLENDER_EXE%
"%BLENDER_EXE%" -b -P "tools\blender_command_center_bake.py" -- --frames 144 --res 900x600 --fps 24
if errorlevel 1 (
  echo [ERROR] Blender bake failed. Existing dashboard assets were not replaced.
  exit /b 1
)

echo.
echo [OK] Jarvis Command Center motion assets are ready.
echo Restart the API gateway if your browser has cached older assets.
exit /b 0
