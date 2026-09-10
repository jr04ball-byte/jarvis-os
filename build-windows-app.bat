@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python required.&pause&exit /b 1)
python -m pip install -r requirements-launcher.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name AI-System launcher.py
if errorlevel 1 (echo Build failed.&pause&exit /b 1)
echo.
echo Built: %~dp0dist\AI-System.exe
echo.
pause
