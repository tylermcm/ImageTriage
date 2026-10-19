<#
Launch Image Triage from source with the Camera Raw development editor and live JSON.
Example: ./scripts/test_camera_raw.ps1 -OpenPath 'C:\Pictures\Test photos'
#>
param(
    [string]$OpenPath,
    [string]$PhotoCraftRoot,
    [ValidateSet('acr18', 'acr-filmstrip', 'acr-glyphs', 'acr-settings-menu', 'acr-main-adjustments', 'acr-calibration-sr')]
    [string]$EditorBuild = 'acr-settings-menu'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $PhotoCraftRoot) {
    $roots = @((Join-Path (Split-Path -Parent $repoRoot) 'photocraft'), (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents/photocraft'))
    $PhotoCraftRoot = $roots | Where-Object { Test-Path -LiteralPath (Join-Path $_ "target/$EditorBuild/debug/photocraft.exe") } | Select-Object -First 1
}
if (-not $PhotoCraftRoot) { throw 'Camera Raw preview build missing. Build the native preview in the companion PhotoCraft checkout first.' }
$PhotoCraftRoot = (Resolve-Path -LiteralPath $PhotoCraftRoot).Path
$editor = Join-Path $PhotoCraftRoot "target/$EditorBuild/debug/photocraft.exe"
$layout = Join-Path $PhotoCraftRoot 'design/camera-raw/layout.json'
$colors = Join-Path $PhotoCraftRoot 'design/camera-raw/tokens.json'
# Older binaries cannot parse newly added live design fields. Keep their validated layouts too.
$frozenDesign = Join-Path $PhotoCraftRoot "target/$EditorBuild/validated-design"
if ($EditorBuild -ne 'acr-settings-menu' -and (Test-Path -LiteralPath (Join-Path $frozenDesign 'layout.json'))) {
    $layout = Join-Path $frozenDesign 'layout.json'
    $colors = Join-Path $frozenDesign 'tokens.json'
}
foreach ($file in @($editor, $layout, $colors)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing local testing input: $file" }
}
$python = @('.msi_build_venv/Scripts/pythonw.exe', '.venv/Scripts/pythonw.exe') | ForEach-Object { Join-Path $repoRoot $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { throw 'Image Triage Python environment missing (.msi_build_venv or .venv).' }
$arguments = @('-m', 'image_triage')
if ($OpenPath) { $arguments += '"' + (Resolve-Path -LiteralPath $OpenPath).Path + '"' }
$testRoot = Join-Path $PhotoCraftRoot "target/$EditorBuild/image-triage-test"
$overrides = @{
    IMAGE_TRIAGE_PHOTOCRAFT_EXE = $editor
    PHOTOCRAFT_CAMERA_RAW_LAYOUT_FILE = $layout
    PHOTOCRAFT_THEME_FILE = $colors
    PHOTOCRAFT_CONFIG_DIR = (Join-Path $testRoot 'editor-profile')
    IMAGE_TRIAGE_LOG_DIR = (Join-Path $testRoot 'logs')
}
$previous = @{}
try {
    foreach ($name in $overrides.Keys) {
        $previous[$name] = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
    }
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repoRoot -PassThru
    Write-Output "Image Triage local Camera Raw test started (launcher PID $($process.Id))."
    Write-Output "Editor: $editor"
    Write-Output "Live layout: $layout"
    Write-Output "Live colors: $colors"
    Write-Output 'Open a photo in the popout, then press E to enter Camera Raw.'
} finally {
    foreach ($name in $previous.Keys) { Set-Item -LiteralPath "Env:$name" -Value $previous[$name].Value }
}
