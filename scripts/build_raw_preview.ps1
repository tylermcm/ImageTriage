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
# Cargo only applies patches from the root manifest. PhotoCraft's path dependencies
# need its pinned Hayro forks too; registry Hayro lacks the craft-pdf APIs.
$pcManifest = Get-Content -LiteralPath "$pcRoot/Cargo.toml" -Raw
$patches = [regex]::Match($pcManifest, '(?ms)^\[patch\.crates-io\][^\r\n]*\r?\n.*?(?=^\[|\z)')
if ($patches.Success) { $manifest += "`n" + $patches.Value }
Set-Content -LiteralPath "$BuildRoot\Cargo.toml" -Value $manifest -Encoding utf8
# Start from the same dependency pins as the editor, then let Cargo add this binary.
if (Test-Path -LiteralPath "$pcRoot/Cargo.lock") {
    Copy-Item -LiteralPath "$pcRoot/Cargo.lock" -Destination "$BuildRoot/Cargo.lock"
}
$env:CARGO_TARGET_DIR = $TargetRoot
& cargo build --manifest-path "$BuildRoot\Cargo.toml"
if ($LASTEXITCODE -ne 0) { throw 'RAW preview worker build failed' }
Copy-Item -LiteralPath "$TargetRoot\debug\image-triage-raw-preview.exe" -Destination "$BuildRoot\image-triage-raw-preview.exe"
Write-Output "Preview worker: $BuildRoot\image-triage-raw-preview.exe"
