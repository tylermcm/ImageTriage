param(
    [string]$HostPython,
    [string]$Runtime,
    [string]$Folder
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
if (-not $Runtime) { $Runtime = Join-Path $sourceRoot 'build\native-cuda-candidate\runtime.json' }
if (-not (Test-Path -LiteralPath $Runtime)) { throw "Prepare a CLI-Culler runtime first, or pass -Runtime: $Runtime" }
if (-not $HostPython) {
    $candidates = @(
        (Join-Path $sourceRoot '.msi_build_venv\Scripts\python.exe'),
        (Join-Path $env:USERPROFILE 'OneDrive\Documents\Playground\.msi_build_venv\Scripts\python.exe')
    )
    $HostPython = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $HostPython) { throw 'Pass -HostPython pointing to the Image Triage development Python with PySide6 installed.' }
$previousRuntime = $env:IMAGE_TRIAGE_CLI_CULLER_RUNTIME
$previousValidation = $env:IMAGE_TRIAGE_CLI_CULLER_VALIDATION
$previousEditor = $env:IMAGE_TRIAGE_NO_EDITOR_PRESTART
Push-Location $sourceRoot
try {
    $env:IMAGE_TRIAGE_CLI_CULLER_RUNTIME = (Resolve-Path -LiteralPath $Runtime).Path
    $env:IMAGE_TRIAGE_CLI_CULLER_VALIDATION = '1'
    $env:IMAGE_TRIAGE_NO_EDITOR_PRESTART = '1'
    $launchArguments = @('-m', 'image_triage')
    if ($Folder) { $launchArguments += @('--folder', $Folder) }
    & $HostPython @launchArguments
    if ($LASTEXITCODE) { throw "Image Triage exited with status $LASTEXITCODE" }
} finally {
    Pop-Location
    $env:IMAGE_TRIAGE_CLI_CULLER_RUNTIME = $previousRuntime
    $env:IMAGE_TRIAGE_CLI_CULLER_VALIDATION = $previousValidation
    $env:IMAGE_TRIAGE_NO_EDITOR_PRESTART = $previousEditor
}
