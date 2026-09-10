@echo off
setlocal
cd /d "%~dp0"
echo.
echo Deepgram API key setup
set /p DGKEY=Paste your NEW Deepgram API key (input is hidden only by the console limitation): 
if "%DGKEY%"=="" (
  echo No key entered.
  exit /b 1
)
if exist .env (
  powershell -NoProfile -Command "$p='.env'; $s=Get-Content -Raw $p; if($s -match '(?m)^DEEPGRAM_API_KEY='){ $s=[regex]::Replace($s,'(?m)^DEEPGRAM_API_KEY=.*$','DEEPGRAM_API_KEY='+$env:DGKEY) } else { $s += \"`r`nDEEPGRAM_API_KEY=\"+$env:DGKEY+\"`r`n\" }; Set-Content -NoNewline -Path $p -Value $s" 
) else (
  >.env echo DEEPGRAM_API_KEY=%DGKEY%
)
echo Deepgram key saved to .env
endlocal
