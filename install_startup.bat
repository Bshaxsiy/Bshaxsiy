@echo off
chcp 65001 >nul
echo ======================================================
echo 🚀 Windows Startup (Avtomatik Ishga Tushish) O'rnatish
echo ======================================================
echo.
powershell -Command "$target = \"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\BshaxsiyBot.vbs\"; $content = \"Set WshShell = CreateObject(`\"WScript.Shell`\")`nWshShell.CurrentDirectory = `\"%~dp0`\"`nWshShell.Run `\"pythonw main.py`\", 0, False\"; Set-Content -Path $target -Value $content"
echo ✅ Windows yoqilishi bilan bot avtomatik ishga tushadigan qilib sozlandi!
echo 💡 Noutbukingiz yoniq bo'lsa, Antigravity yopilgan taqdirda ham bot doim ishlab turadi.
echo.
pause
