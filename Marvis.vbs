' Launches Marvis with no console window at all. Double-click this instead of
' running "python main.py" from a terminal (which keeps that terminal's window
' open as your shell, not something this script can hide).
Set shell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir
shell.Run """" & scriptDir & "\.venv\Scripts\pythonw.exe"" """ & scriptDir & "\main.py""", 0, False
