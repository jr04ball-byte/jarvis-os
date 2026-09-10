@echo off
REM Stop only Python/uvicorn owners of port 8000 to avoid killing unrelated apps.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { $p=Get-Process -Id $_ -ErrorAction SilentlyContinue; if($p -and $p.ProcessName -like '*python*'){ Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Write-Output ('Stopped PID ' + $p.Id) } }"
echo AI System local API stopped.
pause
