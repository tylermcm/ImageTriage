$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepoRoot ".msi_build_venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command py -ErrorAction Stop).Source
    & $Python -3.13 -m sandboxes.face_tagging.cli @args
    exit $LASTEXITCODE
}

& $Python -m sandboxes.face_tagging.cli @args
exit $LASTEXITCODE
