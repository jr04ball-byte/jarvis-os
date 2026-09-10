@echo off
echo ================================================================
echo Docker Desktop Installation Required
echo ================================================================
echo.
echo Your AI system needs Docker Desktop to run.
echo.
echo STEP 1: Download Docker Desktop
echo ----------------------------------------------------------------
echo Opening Docker Desktop download page...
echo.
start https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe
echo.
echo STEP 2: Install Docker Desktop
echo ----------------------------------------------------------------
echo 1. Run the installer that just downloaded
echo 2. Make sure "Use WSL 2 instead of Hyper-V" is checked
echo 3. Complete the installation
echo 4. Restart your computer if prompted
echo.
echo STEP 3: Configure Docker for GPU
echo ----------------------------------------------------------------
echo After installation:
echo 1. Open Docker Desktop
echo 2. Go to Settings ^> Resources ^> WSL Integration
echo 3. Enable your WSL distributions
echo 4. Go to Settings ^> General
echo 5. Ensure "Use the WSL 2 based engine" is checked
echo.
echo STEP 4: Return here and run install.bat
echo ----------------------------------------------------------------
echo Once Docker Desktop is running, run:
echo   install.bat
echo.
echo ================================================================
pause
