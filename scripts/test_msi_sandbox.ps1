<# Run only INSIDE a disposable Windows Sandbox, as its interactive administrator.
   Map installers/scripts read-only at C:\AuditInput and an empty results folder
   writable at C:\AuditResults. Never run installer acceptance on the host.
   The upgrade fixture uses the same payload with a higher test-only MSI version.
#>
param(
    [Parameter(Mandatory=$true)][string]$SandboxComputer,
    [string]$BaseMsi = 'C:\AuditInput\base.msi',
    [string]$UpgradeMsi = 'C:\AuditInput\upgrade.msi'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:USERNAME -ne 'WDAGUtilityAccount' -or $env:COMPUTERNAME -ne $SandboxComputer) {
    throw 'This destructive acceptance test requires the explicitly identified Windows Sandbox account and computer.'
}
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'The sandbox account must be elevated.' }
if (-not (Test-Path -LiteralPath 'C:\AuditResults') -or -not (Test-Path -LiteralPath $BaseMsi) -or
    -not (Test-Path -LiteralPath $UpgradeMsi)) { throw 'Sandbox inputs/results are missing.' }

$results = [Collections.Generic.List[object]]::new()
$installer = New-Object -ComObject WindowsInstaller.Installer
$work = 'C:\MsiAudit'
New-Item -ItemType Directory -Force $work | Out-Null
Copy-Item -LiteralPath $BaseMsi -Destination "$work\base.msi"
Copy-Item -LiteralPath $UpgradeMsi -Destination "$work\upgrade.msi"
$suffixes = Get-Content -LiteralPath 'C:\AuditInput\suffixes.json' -Raw | ConvertFrom-Json

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Read-MsiProperty([string]$Msi, [string]$Name) {
    $db = $installer.OpenDatabase($Msi, 0)
    $view = $db.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property``='$Name'")
    [void]$view.Execute()
    $record = $view.Fetch()
    $value = $record.StringData(1)
    [void]$view.Close()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($db)
    return $value
}
function Invoke-Msi([string]$Phase, [string]$Arguments, [bool]$CleanupExpected) {
    $log = "$work\$Phase.log"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process msiexec.exe -ArgumentList "$Arguments /qn /norestart /L*V `"$log`"" -WindowStyle Hidden -PassThru
    $finished = $process.WaitForExit(300000)
    if (Test-Path -LiteralPath $log) { Copy-Item -LiteralPath $log -Destination 'C:\AuditResults' }
    if (-not $finished) { throw "$Phase timed out; inspect the sandbox before retrying." }
    $process.Refresh()
    $code = $process.ExitCode
    $body = Get-Content -LiteralPath $log -Raw
    $cleanupStarts = ([regex]::Matches($body, '(?m)^Action start [^\r\n]*: A_IMAGE_TRIAGE_CLEANUP\.')).Count
    $entry = [ordered]@{phase=$Phase; exitCode=$code; seconds=$timer.Elapsed.TotalSeconds; cleanupStarts=$cleanupStarts}
    $results.Add($entry)
    $results | ConvertTo-Json -Depth 6 | Set-Content C:\AuditResults\progress.json
    Assert-True ($code -eq 0) "$Phase failed with MSI exit code $code"
    $expected = if ($CleanupExpected) { 1 } else { 0 }
    Assert-True ($cleanupStarts -eq $expected) "$Phase ran cleanup $cleanupStarts times; expected $expected"
}
function Write-Sentinel([string]$Path) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $Path) | Out-Null
    [IO.File]::WriteAllText($Path, 'audit-preserve')
}
$kept = @(
    "$env:APPDATA\Image Triage\Image Triage\decisions.sqlite3",
    "$env:APPDATA\ImageTriage\catalog.sqlite3",
    "$env:APPDATA\ImageTriage\safe-trash\restore.json",
    "$env:USERPROFILE\.image-triage\AI\models\audit-model.bin",
    "$env:USERPROFILE\Pictures\Audit Photos\frame.nef",
    "$env:USERPROFILE\Pictures\Audit Photos\.image_triage_edits\frame.nef.pcraft"
)
$caches = @(
    "$env:LOCALAPPDATA\image_triage_ai_cache\audit-cache.bin",
    "$env:TEMP\image_triage_ai_cache\audit-cache.bin",
    "$env:TEMP\image-triage-association-probes\audit-probe.bin"
)
$settingsKey = 'HKCU:\Software\Image Triage\AuditAcceptance'
$applicationKey = 'HKCU:\Software\Classes\Applications\ImageTriage.exe'
$progIdKey = 'HKCU:\Software\Classes\ImageTriage.SupportedImage'
function Seed-Data {
    foreach ($path in $kept + $caches) { Write-Sentinel $path }
    New-Item -Path $settingsKey -Force | Out-Null
    New-ItemProperty -Path $settingsKey -Name Sentinel -Value 'audit-preserve' -Force | Out-Null
    foreach ($key in @($applicationKey, $progIdKey)) {
        New-Item -Path $key -Force | Out-Null
        New-ItemProperty -Path $key -Name Sentinel -Value 'audit-registration' -Force | Out-Null
    }
    foreach ($suffix in $suffixes) {
        $key = "HKCU:\Software\Classes\$suffix\OpenWithProgids"
        New-Item -Path $key -Force | Out-Null
        New-ItemProperty -Path $key -Name 'ImageTriage.SupportedImage' -Value '' -Force | Out-Null
        New-ItemProperty -Path $key -Name 'Audit.OtherApp' -Value 'keep' -Force | Out-Null
    }
}
function Assert-Data([bool]$Removed) {
    foreach ($path in $kept) {
        Assert-True ((Test-Path -LiteralPath $path) -and [IO.File]::ReadAllText($path) -eq 'audit-preserve') "User data changed: $path"
    }
    Assert-True ((Get-ItemPropertyValue -Path $settingsKey -Name Sentinel) -eq 'audit-preserve') 'Settings changed'
    foreach ($path in $caches) { Assert-True ((Test-Path -LiteralPath $path) -eq (-not $Removed)) "Wrong cache state: $path" }
    foreach ($key in @($applicationKey, $progIdKey)) { Assert-True ((Test-Path -LiteralPath $key) -eq (-not $Removed)) "Wrong registration state: $key" }
    foreach ($suffix in $suffixes) {
        $key = "HKCU:\Software\Classes\$suffix\OpenWithProgids"
        $names = (Get-Item -LiteralPath $key).GetValueNames()
        Assert-True (($names -contains 'ImageTriage.SupportedImage') -eq (-not $Removed)) "Wrong suffix registration: $suffix"
        Assert-True ((Get-ItemPropertyValue -Path $key -Name 'Audit.OtherApp') -eq 'keep') "Other app registration changed: $suffix"
    }
}
function Assert-Installed([string]$Directory) {
    foreach ($relative in @('ImageTriage.exe', 'image_triage_cleanup.exe', 'photocraft\photocraft.exe', 'image-triage-raw-preview.exe')) {
        Assert-True (Test-Path -LiteralPath (Join-Path $Directory $relative)) "Missing installed file: $relative in $Directory"
    }
}
function Assert-Startup([string]$Directory, [string]$Phase) {
    # The shell must not open the deliberate sentinel databases as real SQLite
    # files. Give its screenshot check separate, empty application data.
    $saved = @{}
    foreach ($name in @('APPDATA', 'LOCALAPPDATA', 'IMAGE_TRIAGE_APPDATA', 'QT_QUICK_BACKEND', 'QT_QPA_PLATFORM')) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:APPDATA = "$work\startup\roaming"
        $env:LOCALAPPDATA = "$work\startup\local"
        $env:IMAGE_TRIAGE_APPDATA = "$work\startup\appdata"
        $env:QT_QUICK_BACKEND = 'software'
        $env:QT_QPA_PLATFORM = 'windows'
        New-Item -ItemType Directory -Force $env:APPDATA, $env:LOCALAPPDATA, $env:IMAGE_TRIAGE_APPDATA | Out-Null
        $capture = "C:\AuditResults\$Phase.png"
        $process = Start-Process -FilePath "$Directory\ImageTriage.exe" -ArgumentList "--grab `"$capture`" --grab-wait 2 --size 1200x800" -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "C:\AuditResults\$Phase-stdout.log" -RedirectStandardError "C:\AuditResults\$Phase-stderr.log"
        if (-not $process.WaitForExit(45000)) { throw "$Phase startup timed out" }
        $process.Refresh()
        Assert-True ($process.ExitCode -eq 0 -and (Test-Path -LiteralPath $capture)) "$Phase startup failed"
        $results.Add(@{phase=$Phase; exitCode=$process.ExitCode; screenshot=$capture})
    } finally {
        foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
}
try {
    $baseCode = Read-MsiProperty "$work\base.msi" ProductCode
    $upgradeCode = Read-MsiProperty "$work\upgrade.msi" ProductCode
    Assert-True ($baseCode -ne $upgradeCode) 'Upgrade must use a different ProductCode'
    Assert-True ((Read-MsiProperty "$work\base.msi" UpgradeCode) -eq (Read-MsiProperty "$work\upgrade.msi" UpgradeCode)) 'UpgradeCode mismatch'
    Assert-True ([version](Read-MsiProperty "$work\upgrade.msi" ProductVersion) -gt [version](Read-MsiProperty "$work\base.msi" ProductVersion)) 'Upgrade version must increase'
    Assert-True ($installer.ProductState($baseCode) -ne 5 -and $installer.ProductState($upgradeCode) -ne 5) 'Use a fresh sandbox'
    $custom = 'C:\Audit Custom Install\Image Triage'
    Seed-Data
    Invoke-Msi custom-install "/i `"$work\base.msi`" TARGETDIR=`"$custom`"" $false
    Assert-Installed $custom
    Assert-Startup $custom custom-startup
    Assert-Data $false
    # Remove one installed file only, in the guarded disposable guest. Repair
    # must actually restore it, not merely return success.
    Remove-Item -LiteralPath "$custom\image_triage_cleanup.exe"
    Invoke-Msi custom-repair "/fa $baseCode" $false
    Assert-Installed $custom
    Assert-Data $false
    Invoke-Msi custom-upgrade "/i `"$work\upgrade.msi`" TARGETDIR=`"$custom`"" $false
    Assert-True ($installer.ProductState($baseCode) -ne 5 -and $installer.ProductState($upgradeCode) -eq 5) 'Upgrade did not replace the product'
    Assert-Installed $custom
    Assert-Data $false
    Invoke-Msi custom-uninstall "/x $upgradeCode" $true
    Assert-Data $true
    Assert-True (-not (Test-Path -LiteralPath "$custom\ImageTriage.exe")) 'App remained after uninstall'
    $default = Join-Path $env:ProgramFiles 'ImageTriage'
    Seed-Data
    Invoke-Msi default-install "/i `"$work\base.msi`"" $false
    Assert-Installed $default
    Assert-Data $false
    Invoke-Msi default-uninstall "/x $baseCode" $true
    Assert-Data $true
    Assert-True (-not (Test-Path -LiteralPath "$default\ImageTriage.exe")) 'Default app remained after uninstall'
    @{passed=$true; computer=$env:COMPUTERNAME; user=$env:USERNAME; suffixCount=$suffixes.Count; phases=@($results.ToArray())} |
        ConvertTo-Json -Depth 8 | Set-Content C:\AuditResults\result.json
} catch {
    @{passed=$false; error="$_"; stack=$_.ScriptStackTrace; phases=@($results.ToArray())} |
        ConvertTo-Json -Depth 8 | Set-Content C:\AuditResults\result.json
    throw
}
