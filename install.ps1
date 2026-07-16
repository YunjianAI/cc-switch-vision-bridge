[CmdletBinding()]
param(
    [string] $VisionBaseUrl = "https://api.xiaomimimo.com/v1",
    [string] $VisionModel = "mimo-v2.5",
    [string] $UpstreamBaseUrl = "http://127.0.0.1:15721",
    [string] $ProfilePath,
    [string] $McpConfigPath,
    [string] $McpServerName = "vision",
    [switch] $ConfigureMcp,
    [switch] $NoStart
)

$ErrorActionPreference = "Stop"
$TaskName = "CC Switch Vision Bridge"
$AppDir = Join-Path $env:LOCALAPPDATA "CCSwitchVisionBridge"
$VenvDir = Join-Path $AppDir ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$PythonW = Join-Path $VenvDir "Scripts\pythonw.exe"
$ConfigPath = Join-Path $AppDir "config.toml"
$StatePath = Join-Path $AppDir "state.json"
$PidPath = Join-Path $AppDir "bridge.pid"
$BackupDir = Join-Path $AppDir "backups"
$RepoRoot = $PSScriptRoot

function Write-Utf8NoBom([string] $Path, [string] $Text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Write-JsonAtomic([string] $Path, $Value) {
    $temp = "$Path.$PID.tmp"
    Write-Utf8NoBom $temp ($Value | ConvertTo-Json -Depth 20)
    if (Test-Path -LiteralPath $Path) {
        [IO.File]::Replace($temp, $Path, "$Path.previous", $true)
    } else {
        Move-Item -LiteralPath $temp -Destination $Path
    }
}

function Select-ClaudeProfile {
    $root = Join-Path $env:LOCALAPPDATA "Claude-3p\configLibrary"
    $candidates = @()
    if (Test-Path $root) {
        foreach ($file in Get-ChildItem -LiteralPath $root -Filter "*.json") {
            try {
                $json = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
                $url = [string]$json.inferenceGatewayBaseUrl
                if ($json.inferenceProvider -eq "gateway" -and $url.TrimEnd('/').EndsWith('/claude-desktop')) {
                    $candidates += [pscustomobject]@{ Path = $file.FullName; Url = $url }
                }
            } catch { }
        }
    }
    if ($candidates.Count -eq 0) { throw "No Claude Desktop gateway profile was found." }
    if ($candidates.Count -eq 1) { return $candidates[0].Path }
    Write-Host "Multiple Claude Desktop profiles were found:"
    for ($i = 0; $i -lt $candidates.Count; $i++) {
        Write-Host "[$($i + 1)] $($candidates[$i].Path) -> $($candidates[$i].Url)"
    }
    $choice = [int](Read-Host "Select a profile")
    if ($choice -lt 1 -or $choice -gt $candidates.Count) { throw "Invalid selection." }
    return $candidates[$choice - 1].Path
}

Write-Host "Installing CC Switch Vision Bridge v0.1.1-beta"
New-Item -ItemType Directory -Force -Path $AppDir, $BackupDir | Out-Null

if (-not $ProfilePath) { $ProfilePath = Select-ClaudeProfile }
$ProfilePath = (Resolve-Path -LiteralPath $ProfilePath).Path
$profile = Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json
$originalUrl = [string]$profile.inferenceGatewayBaseUrl
if (-not $originalUrl.TrimEnd('/').EndsWith('/claude-desktop')) {
    throw "Selected profile does not contain a Claude Desktop gateway URL."
}
$previousState = $null
if ($originalUrl -eq "http://127.0.0.1:15722/claude-desktop" -and (Test-Path $StatePath)) {
    $previousState = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $originalUrl = [string]$previousState.original_profile_url
} elseif ($originalUrl -eq "http://127.0.0.1:15722/claude-desktop") {
    $originalUrl = "$($UpstreamBaseUrl.TrimEnd('/'))/claude-desktop"
}

$portOwner = Get-NetTCPConnection -LocalPort 15722 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($portOwner) {
    $owned = $false
    if (Test-Path $PidPath) {
        $recordedPid = [int](Get-Content -LiteralPath $PidPath -Raw)
        if ($recordedPid -eq $portOwner.OwningProcess) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$recordedPid" `
                -ErrorAction SilentlyContinue
            $owned = $proc -and $proc.CommandLine -match 'cc_switch_vision_bridge'
        }
    }
    if (-not $owned) {
        throw "Port 15722 is already used by PID $($portOwner.OwningProcess)."
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Stop-Process -Id $recordedPid -Force
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 250
        if (-not (Get-NetTCPConnection -LocalPort 15722 -State Listen `
                -ErrorAction SilentlyContinue)) { break }
    }
    if (Get-NetTCPConnection -LocalPort 15722 -State Listen -ErrorAction SilentlyContinue) {
        throw "The existing bridge did not release port 15722."
    }
}

$backup = Join-Path $BackupDir ("profile_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
Copy-Item -LiteralPath $ProfilePath -Destination $backup

if (-not (Test-Path $Python)) {
    $basePython = (Get-Command python -ErrorAction Stop).Source
    & $basePython -m venv $VenvDir
}
& $Python -m pip install --disable-pip-version-check --upgrade pip
& $Python -m pip install --disable-pip-version-check "$RepoRoot[mcp]"

$escapedProfile = $ProfilePath.Replace('\', '\\').Replace('"', '\"')
$config = @"
[proxy]
listen_host = "127.0.0.1"
listen_port = 15722
upstream_base_url = "$($UpstreamBaseUrl.TrimEnd('/'))"
max_request_mb = 64
max_upstream_mb = 32

[vision]
base_url = "$($VisionBaseUrl.TrimEnd('/'))"
model = "$VisionModel"
timeout_seconds = 60
max_concurrency = 3
max_image_mb = 20
max_completion_tokens = 1024
retry_count = 1
retry_backoff_seconds = 0.5

[profile]
path = "$escapedProfile"
guard_enabled = true
proxy_base_url = "http://127.0.0.1:15722/claude-desktop"
poll_seconds = 2

[cache]
directory = ""
ttl_hours = 24
enabled = true
"@
Write-Utf8NoBom $ConfigPath $config

if ($env:CCSVB_VISION_API_KEY) {
    $plainKey = $env:CCSVB_VISION_API_KEY
} else {
    $secure = Read-Host "Vision API key" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
$plainKey | & $Python -m cc_switch_vision_bridge.cli --config $ConfigPath set-key --stdin
$plainKey = $null

$mcpEntry = [ordered]@{
    command = $Python
    args = @("-m", "cc_switch_vision_bridge.mcp_launcher")
    env = [ordered]@{ CCSVB_CONFIG = $ConfigPath }
}
$mcp = $null
$mcpEntryExisted = $false
$mcpBackupPath = ""
if ($ConfigureMcp) {
    if (-not $McpConfigPath) { throw "-McpConfigPath is required with -ConfigureMcp." }
    $legacyMcpBackupPath = "$McpConfigPath.ccsvb-backup"
    $existingMcpBackup = ""
    if ($previousState -and
        [string]$previousState.mcp_config_path -eq $McpConfigPath -and
        $previousState.mcp_backup_path -and
        (Test-Path -LiteralPath $previousState.mcp_backup_path)) {
        $existingMcpBackup = [string]$previousState.mcp_backup_path
    } elseif (Test-Path -LiteralPath $legacyMcpBackupPath) {
        $existingMcpBackup = $legacyMcpBackupPath
    }
    if ($existingMcpBackup) {
        $backupRoot = [IO.Path]::GetFullPath($BackupDir).TrimEnd('\') + '\'
        $existingFullPath = [IO.Path]::GetFullPath($existingMcpBackup)
        if ($existingFullPath.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase)) {
            $mcpBackupPath = $existingFullPath
        } else {
            $mcpBackupPath = Join-Path $BackupDir (
                "mcp_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
            )
            Move-Item -LiteralPath $existingFullPath -Destination $mcpBackupPath
        }
    } else {
        $mcpBackupPath = Join-Path $BackupDir (
            "mcp_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss")
        )
    }
    if (Test-Path $McpConfigPath) {
        $mcp = Get-Content -LiteralPath $McpConfigPath -Raw | ConvertFrom-Json
        if (-not (Test-Path $mcpBackupPath)) {
            Copy-Item -LiteralPath $McpConfigPath -Destination $mcpBackupPath
        }
    } else {
        $mcp = [pscustomobject]@{}
    }
    $snapshot = $mcp
    if (Test-Path $mcpBackupPath) {
        $snapshot = Get-Content -LiteralPath $mcpBackupPath -Raw | ConvertFrom-Json
    }
    if ($snapshot.PSObject.Properties['mcpServers'] -and
        $snapshot.mcpServers.PSObject.Properties[$McpServerName]) {
        $mcpEntryExisted = $true
    }
}
$stateMcpConfigPath = if ($ConfigureMcp) { $McpConfigPath } elseif ($previousState) {
    [string]$previousState.mcp_config_path
} else { "" }
$stateMcpServerName = if ($ConfigureMcp) { $McpServerName } elseif ($previousState) {
    [string]$previousState.mcp_server_name
} else { "" }
$stateMcpEntryExisted = if ($ConfigureMcp) { $mcpEntryExisted } elseif ($previousState) {
    [bool]$previousState.mcp_entry_existed
} else { $false }
$stateMcpBackupPath = if ($ConfigureMcp) { $mcpBackupPath } elseif ($previousState) {
    [string]$previousState.mcp_backup_path
} else { "" }
$stateMcpInstalledEntry = if ($ConfigureMcp) { $mcpEntry } elseif ($previousState) {
    $previousState.mcp_installed_entry
} else { $null }

$state = [ordered]@{
    version = "0.1.1-beta"
    installed_at = (Get-Date).ToString("o")
    repo_root = $RepoRoot
    profile_path = $ProfilePath
    original_profile_url = $originalUrl
    proxy_profile_url = "http://127.0.0.1:15722/claude-desktop"
    profile_backup = $backup
    task_name = $TaskName
    mcp_config_path = $stateMcpConfigPath
    mcp_server_name = $stateMcpServerName
    mcp_entry_existed = $stateMcpEntryExisted
    mcp_backup_path = $stateMcpBackupPath
    mcp_installed_entry = $stateMcpInstalledEntry
}
Write-Utf8NoBom $StatePath ($state | ConvertTo-Json -Depth 10)

$profile.inferenceGatewayBaseUrl = $state.proxy_profile_url
$profileTemp = "$ProfilePath.$PID.tmp"
Write-Utf8NoBom $profileTemp ($profile | ConvertTo-Json -Depth 20)
[IO.File]::Replace($profileTemp, $ProfilePath, "$ProfilePath.ccsvb-backup", $true)

$arguments = "-m cc_switch_vision_bridge.cli --config `"$ConfigPath`" run"
$action = New-ScheduledTaskAction -Execute $PythonW -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Local vision preprocessing bridge for Claude Desktop and CC Switch" `
    -Force | Out-Null

$servers = [ordered]@{}
$servers[$McpServerName] = $mcpEntry
$snippet = [ordered]@{ mcpServers = $servers }
Write-Utf8NoBom (Join-Path $AppDir "mcp-config-snippet.json") ($snippet | ConvertTo-Json -Depth 10)

if ($ConfigureMcp) {
    if (-not $mcp.PSObject.Properties['mcpServers']) {
        $mcp | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
    }
    if ($mcp.mcpServers.PSObject.Properties[$McpServerName]) {
        $mcp.mcpServers.$McpServerName = $mcpEntry
    } else {
        $mcp.mcpServers | Add-Member -NotePropertyName $McpServerName -NotePropertyValue $mcpEntry
    }
    Write-JsonAtomic $McpConfigPath $mcp
}

if (-not $NoStart) { Start-ScheduledTask -TaskName $TaskName }
Write-Host "Installed. Restart Claude Desktop, then run .\status.ps1."
