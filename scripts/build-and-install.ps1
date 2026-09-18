# build-and-install.ps1 - full rebuild + install pipeline for dsh-panel.
#
# Why this exists: editing an icon does NOT make cargo re-run the build script
# that embeds the icon resource into the exe, so `npm run tauri build` on its own
# keeps shipping the OLD icon inside the exe (verified: icon.ico was the new
# artwork while the exe still carried the previous one). Dropping the build-script
# cache for this crate is what fixes it.
#
# ASCII only on purpose: this machine decodes BOM-less UTF-8 as GBK.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -SkipIcons
#   powershell -ExecutionPolicy Bypass -File scripts\build-and-install.ps1 -Autostart:$false

param(
    [bool]$Autostart = $true,
    [switch]$SkipIcons
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $SkipIcons) {
    Write-Output "== regenerating icons from tools\source =="
    python tools\make_icons.py build
    npx tauri icon src-tauri\app-icon.png
    Remove-Item 'src-tauri\icons\android', 'src-tauri\icons\ios' -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "== dropping the build-script cache so the icon resource is re-embedded =="
Get-ChildItem 'src-tauri\target\release\build' -Directory -Filter 'dsh-panel-*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
Get-ChildItem 'src-tauri\target\release\.fingerprint' -Directory -Filter 'dsh-panel-*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force }

Write-Output "== building =="
npm run tauri build

Write-Output "== installing =="
if ($Autostart) {
    & "$PSScriptRoot\install.ps1" -Autostart
} else {
    & "$PSScriptRoot\install.ps1"
}
