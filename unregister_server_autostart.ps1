$ErrorActionPreference = "Stop"

$shortcutName = "MySellComb Server AutoStart.lnk"
$startupFolder = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupFolder $shortcutName

if (Test-Path $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
    Write-Output "Removed startup shortcut: $shortcutPath"
} else {
    Write-Output "Startup shortcut not found: $shortcutPath"
}
