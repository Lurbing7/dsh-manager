# install.ps1 - install dsh-panel: copy the release exe to a stable location,
# create Desktop / Start Menu shortcuts, and optionally enable autostart.
#
# ASCII only on purpose: this machine decodes BOM-less UTF-8 as GBK, so non-ASCII
# output here would be mojibake.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Autostart
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Autostart:$false

param(
    [switch]$Autostart,
    [string]$Source = ""
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = Join-Path $projectRoot 'src-tauri\target\release\dsh-panel.exe'
}

if (-not (Test-Path $Source)) {
    Write-Output "ERROR: release exe not found: $Source"
    Write-Output "Build it first:  npm run tauri build"
    exit 1
}

# Per-user install location (same convention as VS Code / Chrome; no admin needed).
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\dsh-panel'
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$target = Join-Path $installDir 'dsh-panel.exe'

# A running instance locks the exe, so stop it before replacing.
Get-Process dsh-panel -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800

Copy-Item $Source $target -Force
Write-Output "installed exe : $target"

$shell = New-Object -ComObject WScript.Shell

function New-Lnk([string]$lnkPath) {
    $sc = $shell.CreateShortcut($lnkPath)
    $sc.TargetPath = $target
    $sc.WorkingDirectory = $installDir
    $sc.Description = 'DSH Panel - DeepSeek Harness update check and launcher'
    $sc.Save()
    Write-Output "shortcut      : $lnkPath"
}

New-Lnk (Join-Path ([Environment]::GetFolderPath('Desktop')) 'DSH Panel.lnk')
New-Lnk (Join-Path ([Environment]::GetFolderPath('Programs')) 'DSH Panel.lnk')

$autostartLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'DSH Panel.lnk'
if ($Autostart) {
    New-Lnk $autostartLnk
    Write-Output "autostart     : ON"
} else {
    if (Test-Path $autostartLnk) { Remove-Item $autostartLnk -Force }
    Write-Output "autostart     : OFF"
}

Write-Output "done."
