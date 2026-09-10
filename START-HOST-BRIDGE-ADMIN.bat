@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "BRIDGE=%~dp0host-bridge\host_bridge.py"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -Verb RunAs -FilePath '%PY%' -ArgumentList '"'%BRIDGE%'"'" 
endlocal
