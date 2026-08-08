param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CalibrationArgs
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "tone_control_calibration.py"
$candidates = @(
    (Join-Path $repoRoot ".msi_build_venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe")
)

foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate)) {
        continue
    }
    $null = & $candidate -c "import numpy; import PIL" 2>&1
    if ($LASTEXITCODE -eq 0) {
        & $candidate $scriptPath @CalibrationArgs
        exit $LASTEXITCODE
    }
}

Write-Error "No project Python with NumPy and Pillow was found. Build or restore .msi_build_venv first."
exit 1
