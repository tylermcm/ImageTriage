param(
    [Parameter(Mandatory=$true)][string]$PhotoCraftRoot,
    [string]$BuildRoot = 'K:\Builds\ImageTriage\raw-preview',
    [string]$TargetRoot = 'K:\Builds\PhotoCraft\target\merge-v0.20.1-20261103'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = (Join-Path $repoRoot 'native\raw_preview').Replace('\','/')
$pcRoot = (Resolve-Path -LiteralPath $PhotoCraftRoot).Path.Replace('\','/')
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$manifest = (Get-Content -LiteralPath "$repoRoot\native\raw_preview\Cargo.toml.in" -Raw).Replace('@PHOTOCRAFT@',$pcRoot).Replace('@SOURCE@',$sourceRoot)
Set-Content -LiteralPath "$BuildRoot\Cargo.toml" -Value $manifest -Encoding utf8
$env:CARGO_TARGET_DIR = $TargetRoot
& cargo build --manifest-path "$BuildRoot\Cargo.toml"
if ($LASTEXITCODE -ne 0) { throw 'RAW preview worker build failed' }
Copy-Item -LiteralPath "$TargetRoot\debug\image-triage-raw-preview.exe" -Destination "$BuildRoot\image-triage-raw-preview.exe"
Write-Output "Preview worker: $BuildRoot\image-triage-raw-preview.exe"
