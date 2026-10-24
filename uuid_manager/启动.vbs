Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

' 获取当前目录
strDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strDir

' 优先使用本地 electron.exe（原生无控制台启动）
electronExe = strDir & "\node_modules\electron\dist\electron.exe"
If FSO.FileExists(electronExe) Then
    ' 0 = 隐藏窗口，False = 不等待进程结束
    WshShell.Run Chr(34) & electronExe & Chr(34) & " " & Chr(34) & strDir & Chr(34), 0, False
    WScript.Quit 0
End If

' 回退：使用 node 启动
nodeExe = "C:\Program Files\nodejs\node.exe"
If Not FSO.FileExists(nodeExe) Then nodeExe = "node"
cliJs = strDir & "\node_modules\electron\cli.js"
If Not FSO.FileExists(cliJs) Then
    MsgBox "未找到 electron 依赖，请先运行 npm install", 16, "启动失败"
    WScript.Quit 1
End If

cmd = Chr(34) & nodeExe & Chr(34) & " " & Chr(34) & cliJs & Chr(34) & " " & Chr(34) & strDir & Chr(34)
WshShell.Run cmd, 0, False
