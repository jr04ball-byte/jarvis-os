@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ==================================================
echo  JARVIS V23 - FULL RELEASE / STABILITY GATE
echo ==================================================
echo.

set "PYTHON_EXE="
where py.exe >nul 2>nul && set "PYTHON_EXE=py -3"
if not defined PYTHON_EXE where python.exe >nul 2>nul && set "PYTHON_EXE=python"
if not defined PYTHON_EXE (
  echo [FAIL] Python 3 was not found.
  exit /b 2
)

set "FAILED=0"

call :run "Python tests" %PYTHON_EXE% -m pytest -q
call :run "Python compile" %PYTHON_EXE% -m compileall -q api-gateway tests tools

where node.exe >nul 2>nul
if errorlevel 1 (
  echo [WARN] Node.js not found - voice-engine tests skipped.
) else (
  call :run "Voice engine tests" node --test tests\voice-engine.test.js
)

where ffprobe.exe >nul 2>nul
if errorlevel 1 (
  echo [WARN] FFprobe not found - command-center media probe skipped.
) else (
  call :run "Command Center WebM probe" ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height -show_entries format=duration -of default=noprint_wrappers=1 api-gateway\assets\command-center-loop.webm
  call :run "Command Center MP4 probe" ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height -show_entries format=duration -of default=noprint_wrappers=1 api-gateway\assets\command-center-loop.mp4
)

where docker.exe >nul 2>nul
if errorlevel 1 (
  echo [WARN] Docker CLI not found - Docker Compose config check skipped.
) else (
  call :run "Docker Compose config" docker compose config -q
)

where blender.exe >nul 2>nul
if errorlevel 1 (
  echo [INFO] Blender not on PATH. The dashboard uses included animated assets.
  echo        Run BAKE-JARVIS-DASHBOARD.bat after Blender is installed to create the richer scene.
) else (
  call :run "Blender availability" blender --version
)

echo.
if "%FAILED%"=="0" (
  echo ==================================================
  echo [PASS] JARVIS V23 CORE RELEASE GATE PASSED
  echo ==================================================
  exit /b 0
) else (
  echo ==================================================
  echo [FAIL] JARVIS V23 RELEASE GATE HAS FAILURES
  echo ==================================================
  exit /b 1
)

:run
set "NAME=%~1"
shift
echo.
echo --- %NAME% ---
%*
if errorlevel 1 (
  echo [FAIL] %NAME%
  set "FAILED=1"
) else (
  echo [PASS] %NAME%
)
exit /b 0
