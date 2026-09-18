# install.ps1 - install dsh-manager: copy the release exe to a stable location,
# create Desktop / Start Menu shortcuts, and optionally enable autostart.
#
# ASCII only on purpose: this machine decodes BOM-less UTF-8 as GBK, so non-ASCII
# output here would be mojibake.
#
# Renamed from dsh-panel: the app now also does usage analysis, plugin management
# and the Feishu bridge, so "panel" undersold it. The old install is removed here
# so a machine does not end up with two shortcuts pointing at two exes.
#
# NOTE: the bundle identifier stays `com.dshpanel.app` on purpose - that is the
# app-data directory holding the local usage store, and changing it would look
# like all history vanished.
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

$appName = 'dsh-manager'
$appTitle = 'DSH Manager'
$oldName = 'dsh-panel'
$oldTitle = 'DSH Panel'

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = Join-Path $projectRoot "src-tauri\target\release\$appName.exe"
}

if (-not (Test-Path $Source)) {
    Write-Output "ERROR: release exe not found: $Source"
    Write-Output "Build it first:  npm run tauri build"
    exit 1
}

$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$startup = [Environment]::GetFolderPath('Startup')

# --- remove the previous dsh-panel install -------------------------------
# Shortcuts first: a stale one would keep launching the old exe.
foreach ($lnk in @(
    (Join-Path $desktop "$oldTitle.lnk"),
    (Join-Path $programs "$oldTitle.lnk"),
    (Join-Path $startup "$oldTitle.lnk")
)) {
    if (Test-Path $lnk) {
        Remove-Item $lnk -Force
        Write-Output "removed old   : $lnk"
    }
}

Get-Process $oldName -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
$oldDir = Join-Path $env:LOCALAPPDATA "Programs\$oldName"
if (Test-Path $oldDir) {
    Remove-Item $oldDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "removed old   : $oldDir"
}

# --- install --------------------------------------------------------------
# Per-user install location (same convention as VS Code / Chrome; no admin needed).
$installDir = Join-Path $env:LOCALAPPDATA "Programs\$appName"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$target = Join-Path $installDir "$appName.exe"

# A running instance locks the exe, so stop it before replacing.
Get-Process $appName -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800

Copy-Item $Source $target -Force
Write-Output "installed exe : $target"

$shell = New-Object -ComObject WScript.Shell

function New-Lnk([string]$lnkPath) {
    $sc = $shell.CreateShortcut($lnkPath)
    $sc.TargetPath = $target
    $sc.WorkingDirectory = $installDir
    $sc.Description = 'DSH Manager - DeepSeek Harness dashboard, launcher and manager'
    $sc.Save()
    Write-Output "shortcut      : $lnkPath"
}

New-Lnk (Join-Path $desktop "$appTitle.lnk")
New-Lnk (Join-Path $programs "$appTitle.lnk")

$autostartLnk = Join-Path $startup "$appTitle.lnk"
if ($Autostart) {
    New-Lnk $autostartLnk
    Write-Output "autostart     : ON"
} else {
    if (Test-Path $autostartLnk) { Remove-Item $autostartLnk -Force }
    Write-Output "autostart     : OFF"
}

# Windows caches shell icons by path and ignores the file's timestamp, so a
# freshly rebuilt exe keeps showing the OLD icon until the cache is nudged.
Start-Process 'ie4uinit.exe' -ArgumentList '-show' -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
Write-Output "icon cache    : refreshed"

Write-Output "done."
