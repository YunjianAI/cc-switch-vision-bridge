[CmdletBinding()]
param()

$AppDir = Join-Path $env:LOCALAPPDATA "CCSwitchVisionBridge"
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"
$Config = Join-Path $AppDir "config.toml"
$State = Join-Path $AppDir "state.json"
$TaskName = "CC Switch Vision Bridge"
$WatchdogTaskName = "CC Switch Vision Bridge Watchdog"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task: $($task.State), last result: $($info.LastTaskResult)"
} else {
    Write-Host "Task: not installed"
}
$watchdog = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction SilentlyContinue
if ($watchdog) {
    $watchdogInfo = Get-ScheduledTaskInfo -TaskName $WatchdogTaskName
    Write-Host "Watchdog: $($watchdog.State), last result: $($watchdogInfo.LastTaskResult)"
} else {
    Write-Host "Watchdog: not installed"
}
if (Test-Path $State) {
    $stateJson = Get-Content -LiteralPath $State -Raw | ConvertFrom-Json
    Write-Host "Profile: $($stateJson.profile_path)"
}
if ((Test-Path $Python) -and (Test-Path $Config)) {
    & $Python -m cc_switch_vision_bridge.cli --config $Config status
    exit $LASTEXITCODE
}
Write-Host "Bridge installation is incomplete."
exit 1
