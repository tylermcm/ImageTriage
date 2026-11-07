param(
    [Parameter(Mandatory=$true)][string]$PhotoCraftRoot,
    [string]$BuildRoot = 'K:\Builds\ImageTriage\raw-preview-spike',
    [string]$TargetRoot = 'K:\Builds\PhotoCraft\target\merge-v0.20.1-20261103'
)
$ErrorActionPreference = 'Stop'
$pcRoot = (Resolve-Path -LiteralPath $PhotoCraftRoot).Path.Replace('\','/')
$sourceRoot = $PSScriptRoot.Replace('\','/')
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$manifest = (Get-Content -LiteralPath "$PSScriptRoot\Cargo.toml.in" -Raw).Replace('@PHOTOCRAFT@',$pcRoot).Replace('@SOURCE@',$sourceRoot)
Set-Content -LiteralPath "$BuildRoot\Cargo.toml" -Value $manifest -Encoding utf8
$env:CARGO_TARGET_DIR = $TargetRoot
& cargo build --manifest-path "$BuildRoot\Cargo.toml"
if ($LASTEXITCODE -ne 0) { throw 'Preview spike build failed' }
Write-Output "Backend: $TargetRoot\debug\triage-raw-preview-spike.exe"
