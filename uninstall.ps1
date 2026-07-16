[CmdletBinding()]
param([switch] $KeepCredentials)

$ErrorActionPreference = "Stop"
$TaskName = "CC Switch Vision Bridge"
$AppDir = Join-Path $env:LOCALAPPDATA "CCSwitchVisionBridge"
$StatePath = Join-Path $AppDir "state.json"
$PidPath = Join-Path $AppDir "bridge.pid"
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"

if (Test-Path $PidPath) {
    $bridgePid = [int](Get-Content -LiteralPath $PidPath -Raw)
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$bridgePid" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match 'cc_switch_vision_bridge') {
        Stop-Process -Id $bridgePid -Force
    }
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if (Test-Path $StatePath) {
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    if (Test-Path -LiteralPath $state.profile_path) {
        $profile = Get-Content -LiteralPath $state.profile_path -Raw | ConvertFrom-Json
        if ($profile.inferenceGatewayBaseUrl -eq $state.proxy_profile_url) {
            $profile.inferenceGatewayBaseUrl = $state.original_profile_url
            $temp = "$($state.profile_path).$PID.tmp"
            $encoding = New-Object System.Text.UTF8Encoding($false)
            [IO.File]::WriteAllText($temp, ($profile | ConvertTo-Json -Depth 20), $encoding)
            [IO.File]::Replace($temp, $state.profile_path, "$($state.profile_path).ccsvb-uninstall-backup", $true)
            Write-Host "Claude profile restored to $($state.original_profile_url)"
        } else {
            Write-Host "Profile URL was changed by another program; it was not overwritten."
        }
    }
}

if (-not $KeepCredentials -and (Test-Path $Python)) {
    & $Python -m cc_switch_vision_bridge.cli delete-key
}

$archive = Join-Path $env:LOCALAPPDATA ("CCSwitchVisionBridge-uninstalled-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
if (Test-Path $AppDir) { Move-Item -LiteralPath $AppDir -Destination $archive }
Write-Host "Uninstalled. Recovery files retained at: $archive"
