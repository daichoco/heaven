' タスクスケジューラ用。PowerShellのウィンドウを一切表示せずに poll-heaven-job.ps1 を実行する。
Set shell = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File """ & dir & "\poll-heaven-job.ps1""", 0, False
