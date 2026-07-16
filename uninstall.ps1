[CmdletBinding()]
param([switch] $KeepCredentials)

$ErrorActionPreference = "Stop"
$TaskName = "CC Switch Vision Bridge"
$AppDir = Join-Path $env:LOCALAPPDATA "CCSwitchVisionBridge"
$StatePath = Join-Path $AppDir "state.json"
$PidPath = Join-Path $AppDir "bridge.pid"
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"

function Write-Utf8NoBom([string] $Path, [string] $Text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Write-JsonAtomic([string] $Path, $Value) {
    $temp = "$Path.$PID.tmp"
    Write-Utf8NoBom $temp ($Value | ConvertTo-Json -Depth 20)
    if (Test-Path -LiteralPath $Path) {
        [IO.File]::Replace($temp, $Path, "$Path.ccsvb-uninstall-backup", $true)
    } else {
        Move-Item -LiteralPath $temp -Destination $Path
    }
}

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
            Write-JsonAtomic $state.profile_path $profile
            Write-Host "Claude profile restored to $($state.original_profile_url)"
        } else {
            Write-Host "Profile URL was changed by another program; it was not overwritten."
        }
    }

    if ($state.mcp_config_path -and (Test-Path -LiteralPath $state.mcp_config_path)) {
        $mcp = Get-Content -LiteralPath $state.mcp_config_path -Raw | ConvertFrom-Json
        $name = [string]$state.mcp_server_name
        $current = $null
        if ($mcp.PSObject.Properties['mcpServers'] -and
            $mcp.mcpServers.PSObject.Properties[$name]) {
            $current = $mcp.mcpServers.$name
        }
        $owned = $current -and
            $current.command -eq $state.mcp_installed_entry.command -and
            (($current.args -join "`0") -eq ($state.mcp_installed_entry.args -join "`0")) -and
            $current.env.CCSVB_CONFIG -eq $state.mcp_installed_entry.env.CCSVB_CONFIG
        if ($owned) {
            if ($state.mcp_entry_existed) {
                if (-not $state.mcp_backup_path -or
                    -not (Test-Path -LiteralPath $state.mcp_backup_path)) {
                    throw "MCP backup is missing; '$name' was not overwritten."
                }
                $backupMcp = Get-Content -LiteralPath $state.mcp_backup_path -Raw |
                    ConvertFrom-Json
                if (-not $backupMcp.PSObject.Properties['mcpServers'] -or
                    -not $backupMcp.mcpServers.PSObject.Properties[$name]) {
                    throw "MCP backup does not contain '$name'; current config was not overwritten."
                }
                $mcp.mcpServers.$name = $backupMcp.mcpServers.$name
            } else {
                $mcp.mcpServers.PSObject.Properties.Remove($name)
            }
            Write-JsonAtomic $state.mcp_config_path $mcp
            Write-Host "MCP entry '$name' restored."
        } else {
            Write-Host "MCP entry '$name' was changed by another program; it was not overwritten."
        }
    }
}

if (-not $KeepCredentials -and (Test-Path $Python)) {
    & $Python -m cc_switch_vision_bridge.cli delete-key
}

$archive = Join-Path $env:LOCALAPPDATA ("CCSwitchVisionBridge-uninstalled-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
if (Test-Path $AppDir) { Move-Item -LiteralPath $AppDir -Destination $archive }
Write-Host "Uninstalled. Recovery files retained at: $archive"
