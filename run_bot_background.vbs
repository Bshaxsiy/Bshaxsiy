Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "c:\Users\b.maxkamboev\Desktop\antigravity google"
WshShell.Run "pythonw main.py", 0, False
