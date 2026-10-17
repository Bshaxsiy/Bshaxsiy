@echo off
chcp 65001 >nul
echo ======================================================
echo 🗑 Windows Startup (Avtomatik Ishga Tushish) ni O'chirish
echo ======================================================
echo.
powershell -Command "$target = \"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\BshaxsiyBot.vbs\"; if (Test-Path $target) { Remove-Item $target -Force; Write-Host 'Avtomatik ishga tushish o''chirildi.' } else { Write-Host 'Fayl topilmadi.' }"
echo.
pause
