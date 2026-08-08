$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepoRoot ".msi_build_venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command py -ErrorAction Stop).Source
    & $Python -3.13 -m sandboxes.grounded_sam.cli @args
    exit $LASTEXITCODE
}

& $Python -m sandboxes.grounded_sam.cli @args
exit $LASTEXITCODE
