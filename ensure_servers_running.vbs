Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run Chr(34) & shell.CurrentDirectory & "\ensure_servers_running.cmd" & Chr(34), 0, False
