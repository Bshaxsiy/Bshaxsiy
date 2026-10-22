@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================
echo [Telegram Bot] Windows Startup Sozlash
echo ======================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$startupPath = [IO.Path]::Combine($env:APPDATA, 'Microsoft\Windows\Start Menu\Programs\Startup\BshaxsiyBot.vbs'); $botDir = (Get-Location).Path; $pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if (-not $pythonw) { $pythonw = 'C:\Users\b.maxkamboev\AppData\Local\Programs\Python\Python312\pythonw.exe' }; $content = @'
Set WshShell = CreateObject(`"WScript.Shell`")
WshShell.CurrentDirectory = `"{0}`"
WshShell.Run `"`"`"{1}`"`" `"`"{0}\main.py`"`"`, 0, False
'@ -f $botDir, $pythonw; [IO.File]::WriteAllText($startupPath, $content, [Text.Encoding]::UTF8)"
echo [OK] Windows yoqilishi bilan bot avtomatik ishga tushadi!
echo [INFO] Kompyuteringiz yoniq bo'lsa, Antigravity yopilgan taqdirda ham bot doim ishlab turadi.
echo.
pause