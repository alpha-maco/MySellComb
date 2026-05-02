$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSCommandPath
$shortcutName = "MySellComb Server AutoStart.lnk"
$wscriptPath = Join-Path $env:SystemRoot "System32\wscript.exe"
$runnerPath = Join-Path $projectRoot "ensure_servers_running.vbs"
$startupFolder = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupFolder $shortcutName

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $wscriptPath
$shortcut.Arguments = ('"{0}"' -f $runnerPath)
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.IconLocation = "$wscriptPath,0"
$shortcut.Description = "Auto-start Live and Hb servers at Windows logon"
$shortcut.Save()

Write-Output "Registered startup shortcut: $shortcutPath"
