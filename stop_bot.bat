@echo off
chcp 65001 >nul
echo ======================================================
echo 🛑 Telegram Botni To'xtatish
echo ======================================================
echo.
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host 'Jarayon to''xtatildi PID:' $_.ProcessId }"
echo.
echo ✅ Bot muvaffaqiyatli to'xtatildi.
echo.
pause
