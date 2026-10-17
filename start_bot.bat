@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================
echo 🤖 Telegram Botni Fondan Ishga Tushirish
echo ======================================================
echo.

:: Agar allaqachon ishlayotgan bo'lsa tekshiramiz
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*main.py*' } | Select-Object -First 1" >nul 2>&1

start "" "C:\Users\b.maxkamboev\AppData\Local\Programs\Python\Python312\pythonw.exe" main.py

echo ✅ Bot orqa fonda (yashirin) muvaffaqiyatli ishga tushirildi!
echo 💡 Antigravity yoki ushbu oynani yopsangiz ham bot ishlashda davom etadi.
echo.
timeout /t 4 >nul
exit
