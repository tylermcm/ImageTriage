# The installed 2.4.0 baseline plus the Library and review-workflow fixes.
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path (Split-Path -Parent $PSScriptRoot) '.msi_build_venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'image_triage\qml_shell\app.py'))) {
    throw "The current UI checkout is missing: $sourceRoot"
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "The development Python environment is missing: $pythonPath"
}
Push-Location -LiteralPath $sourceRoot
try {
    & $pythonPath -m image_triage.qml_shell @args
    $runExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $runExitCode
