@echo off
setlocal
if "%AI_HOST_BRIDGE_TOKEN%"=="" (
  echo Set AI_HOST_BRIDGE_TOKEN first.
  echo Example: setx AI_HOST_BRIDGE_TOKEN "a-long-random-token"
  exit /b 1
)
cd /d "%~dp0"
if "%AI_HOST_FILES%"=="" echo WARNING: AI_HOST_FILES is not set; /host-files paths cannot be opened.
if "%AI_COMPUTER_CONTROL%"=="true" echo COMPUTER CONTROL IS ENABLED. Use only on your own machine.
python -m pip install -r requirements.txt
python host_bridge.py
