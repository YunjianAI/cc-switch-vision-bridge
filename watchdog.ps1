[CmdletBinding()]
param(
    [string] $BridgeTaskName = "CC Switch Vision Bridge",
    [int] $BridgePort = 15722
)

$ErrorActionPreference = "Stop"
$AppDir = Join-Path $env:LOCALAPPDATA "CCSwitchVisionBridge"
$PidPath = Join-Path $AppDir "bridge.pid"

if (Get-NetTCPConnection -LocalPort $BridgePort -State Listen -ErrorAction SilentlyContinue) {
    exit 0
}

$task = Get-ScheduledTask -TaskName $BridgeTaskName -ErrorAction SilentlyContinue
if (-not $task) {
    throw "Bridge task '$BridgeTaskName' is not installed."
}

if (Test-Path -LiteralPath $PidPath) {
    $bridgePid = [int](Get-Content -LiteralPath $PidPath -Raw)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$bridgePid" `
        -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -match "cc_switch_vision_bridge") {
        Stop-Process -Id $bridgePid -Force -ErrorAction SilentlyContinue
    }
}

Stop-ScheduledTask -TaskName $BridgeTaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $BridgeTaskName

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -LocalPort $BridgePort -State Listen `
            -ErrorAction SilentlyContinue) {
        exit 0
    }
}

throw "Bridge task did not reopen port $BridgePort."
