@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT=%DESKTOP%\Jarvis AI.lnk"
powershell -NoProfile -Command "$WshShell=New-Object -ComObject WScript.Shell; $S=$WshShell.CreateShortcut('%SHORTCUT%'); $S.TargetPath='%SCRIPT_DIR%OPEN-JARVIS.bat'; $S.WorkingDirectory='%SCRIPT_DIR%'; $S.Description='Launch Jarvis AI System'; $S.IconLocation='%SystemRoot%\System32\SHELL32.dll,13'; $S.Save()"
if exist "%SHORTCUT%" (
 echo.
 echo Jarvis desktop shortcut created:
 echo %SHORTCUT%
 echo.
 echo Double-click "Jarvis AI" on your desktop to open Jarvis.
) else (
 echo Failed to create the desktop shortcut.
)
pause
