Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
strDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strDir
electronExe = strDir & "\node_modules\electron\dist\electron.exe"
ret = WshShell.Run(Chr(34) & electronExe & Chr(34) & " " & Chr(34) & strDir & Chr(34), 1, True)
MsgBox "Exit code: " & ret, 64, "Done"
