@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================
echo [Telegram Bot] Fondan Ishga Tushirish
echo ======================================================
echo.

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*python*' -and $_.CommandLine -like '*main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

start "" "C:\Users\b.maxkamboev\AppData\Local\Programs\Python\Python312\pythonw.exe" "%~dp0main.py"

echo [OK] Bot orqa fonda muvaffaqiyatli ishga tushirildi!
echo [INFO] Antigravity yoki ushbu oynani yopsangiz ham bot ishlashda davom etadi.
echo.
ping 127.0.0.1 -n 3 >nul
exit