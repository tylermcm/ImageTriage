param(
    [Parameter(Mandatory=$true)][string]$RawFile,
    [string]$HostPython,
    [string]$Backend = 'K:\Builds\PhotoCraft\target\merge-v0.20.1-20261103\debug\triage-raw-preview-spike.exe',
    [ValidateSet(900,1200,1800,2400)][int]$Edge = 1200
)
$ErrorActionPreference = 'Stop'
if (-not $HostPython) {
    $repoRoot = (Resolve-Path "$PSScriptRoot\..\..\..").Path
    $candidates = @(
        (Join-Path $repoRoot '.msi_build_venv\Scripts\python.exe'),
        (Join-Path $env:USERPROFILE 'OneDrive\Documents\Playground\.msi_build_venv\Scripts\python.exe')
    )
    $HostPython = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $HostPython) { throw 'Pass -HostPython with PySide6 installed.' }
if (-not (Test-Path -LiteralPath $Backend -PathType Leaf)) { throw 'Build the isolated spike backend first using build.ps1.' }
$photo = (Resolve-Path -LiteralPath $RawFile).Path
$previousStyle = $env:QT_QUICK_CONTROLS_STYLE
$previousPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QUICK_CONTROLS_STYLE = 'Basic'
    $env:QT_QPA_PLATFORM = 'windows'
    & $HostPython "$PSScriptRoot\spike.py" $photo --backend $Backend --edge $Edge
    if ($LASTEXITCODE) { throw "Preview spike exited with $LASTEXITCODE" }
} finally {
    $env:QT_QUICK_CONTROLS_STYLE = $previousStyle
    $env:QT_QPA_PLATFORM = $previousPlatform
}
