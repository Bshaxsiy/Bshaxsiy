@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================
echo [Telegram Bot] To'xtatish
echo ======================================================
echo.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*python*' -and $_.CommandLine -like '*main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Jarayon to''xtatildi PID: ' + $_.ProcessId) }"
echo.
echo [OK] Bot muvaffaqiyatli to'xtatildi.
echo.
pause