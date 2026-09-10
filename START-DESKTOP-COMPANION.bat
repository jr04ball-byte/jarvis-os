@echo off
setlocal
cd /d "%~dp0"
if not exist desktop-companion\node_modules\electron\dist\electron.exe (
  echo Electron is not installed in desktop-companion.
  echo Run: cd desktop-companion ^&^& npm install
  pause
  exit /b 1
)
cd desktop-companion
npx electron .
