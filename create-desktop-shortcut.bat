@echo off
REM Create Desktop Shortcut for AI System

set SCRIPT_DIR=%~dp0
set DESKTOP=%USERPROFILE%\Desktop
set SHORTCUT=%DESKTOP%\AI System.lnk

powershell -Command "$WshShell = New-Object -comObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%SHORTCUT%'); $Shortcut.TargetPath = '%SCRIPT_DIR%AI-System.bat'; $Shortcut.WorkingDirectory = '%SCRIPT_DIR%'; $Shortcut.Description = 'Launch AI System'; $Shortcut.Save()"

if exist "%SHORTCUT%" (
    echo ✅ Desktop shortcut created successfully!
    echo.
    echo Shortcut location: %DESKTOP%\AI System.lnk
) else (
    echo ❌ Failed to create shortcut
)

pause
