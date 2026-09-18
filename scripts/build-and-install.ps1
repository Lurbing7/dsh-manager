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

# NOTE: deliberately NOT $ErrorActionPreference = 'Stop'. The tauri CLI writes its
# progress to stderr, and PowerShell turns native stderr into a terminating
# NativeCommandError - that would abort this script partway through the build.
# Each step's exit code is checked explicitly instead.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Assert-LastExit([string]$step) {
    if ($LASTEXITCODE -ne 0) {
        Write-Output "ERROR: $step failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

if (-not $SkipIcons) {
    Write-Output "== regenerating icons from tools\source =="
    python tools\make_icons.py build
    Assert-LastExit 'make_icons.py'
    npx tauri icon src-tauri\app-icon.png
    Assert-LastExit 'tauri icon'
    Remove-Item 'src-tauri\icons\android', 'src-tauri\icons\ios' -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "== dropping the build-script cache so the icon resource is re-embedded =="
# Filter follows the crate name (dsh-manager after the rename): a stale filter
# silently matches nothing, and the old icon keeps getting embedded.
Get-ChildItem 'src-tauri\target\release\build' -Directory -Filter 'dsh-manager-*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
Get-ChildItem 'src-tauri\target\release\.fingerprint' -Directory -Filter 'dsh-manager-*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force }

Write-Output "== building =="
npm run tauri build
Assert-LastExit 'tauri build'

Write-Output "== installing =="
if ($Autostart) {
    & "$PSScriptRoot\install.ps1" -Autostart
} else {
    & "$PSScriptRoot\install.ps1"
}
Assert-LastExit 'install.ps1'
Write-Output "== done =="
