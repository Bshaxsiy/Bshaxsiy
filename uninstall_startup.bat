@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================
echo [Telegram Bot] Windows Startup ni O'chirish
echo ======================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$startupPath = [IO.Path]::Combine($env:APPDATA, 'Microsoft\Windows\Start Menu\Programs\Startup\BshaxsiyBot.vbs'); if (Test-Path $startupPath) { Remove-Item $startupPath -Force; Write-Host '[OK] Avtomatik ishga tushish o''chirildi.' } else { Write-Host '[INFO] Startup fayli topilmadi.' }"
echo.
pause