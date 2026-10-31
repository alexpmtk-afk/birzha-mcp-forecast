param(
    [string]$QueueRoot = 'C:\ProgramData\ChatGPT-PK\BirzhaControlQueue',
    [int]$IntervalSeconds = 3600,
    [int]$InitialDelaySeconds = 45,
    [string]$McpHelperPath = ""
)

$ErrorActionPreference = 'Stop'

$ExpectedComputer = 'DESKTOP-7F6KPIL'
$ExpectedUserSuffix = '\Win10_Game_OS'
$Repo = 'C:\MCP-HOME\projects\birzha-mcp-forecast'
$VenvPython = Join-Path $Repo '.venv\Scripts\python.exe'
$PolicyRoot = Split-Path -Parent $PSCommandPath
$RefreshScriptPath = $PSCommandPath
$McpHelper = if($McpHelperPath){$McpHelperPath}else{Join-Path $env:LOCALAPPDATA 'ChatGPT-PK\BirzhaControl\birzha_mcp_client.py'}
$LogRoot = 'C:\MCP-HOME\logs\birzha'
$LogPath = Join-Path $LogRoot 'auto-history-refresh.log'
$PidPath = Join-Path $QueueRoot 'history-refresh.pid'
$TempRoot = Join-Path $QueueRoot 'temp'

function Write-RefreshLog {
    param([string]$Message)
    New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
    if(Test-Path -LiteralPath $LogPath -PathType Leaf){
        $item=Get-Item -LiteralPath $LogPath
        if($item.Length -gt 5242880){
            $archive=Join-Path $LogRoot ('auto-history-refresh-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
            Move-Item -LiteralPath $LogPath -Destination $archive -Force
        }
    }
    Add-Content -LiteralPath $LogPath -Value (('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),$Message)) -Encoding UTF8
}

function Enter-RefreshLease {
    param([string]$Name = 'Global\BirzhaHistoryAndMirrorRefresh')
    $lease = [Threading.Mutex]::new($false, $Name)
    try {
        try { $acquired = $lease.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if(-not $acquired){ $lease.Dispose(); return $null }
        return ,$lease
    }
    catch { $lease.Dispose(); throw }
}

function Get-SafeTill {
    $tz=[TimeZoneInfo]::FindSystemTimeZoneById('Russian Standard Time')
    $moscow=[TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow,$tz)
    $lagDays=if($moscow.TimeOfDay -ge [TimeSpan]::FromHours(10)){1}else{2}
    return $moscow.Date.AddDays(-$lagDays)
}

function Invoke-RefreshMcp {
    param([object]$Request)
    $requestPath=Join-Path $TempRoot ('refresh-'+[Guid]::NewGuid().ToString('N')+'.json')
    try {
        $Request | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $requestPath -Encoding UTF8
        $output=@(& $VenvPython $McpHelper --request $requestPath 2>&1)
        $helperExit=$LASTEXITCODE
        $last=@($output | Where-Object {-not [string]::IsNullOrWhiteSpace([string]$_)} | Select-Object -Last 1)
        if($helperExit -ne 0 -or $last.Count -eq 0){throw 'MCP_HELPER_FAILED'}
        $payload=([string]$last[0]) | ConvertFrom-Json
        if($payload.status -ne 'PASS'){throw 'MCP_REQUEST_FAILED'}
        return $payload
    } finally {Remove-Item -LiteralPath $requestPath -Force -ErrorAction SilentlyContinue}
}

function Assert-RefreshReadiness {
    # Repository update alone does not replace the registered external runtime copy.
    $canonical=Join-Path $Repo 'scripts\run_home_history_and_mirror.ps1'
    if(-not(Test-Path -LiteralPath $canonical -PathType Leaf)){throw 'REFRESH_SOURCE_MISSING'}
    if((Get-FileHash -LiteralPath $canonical -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $RefreshScriptPath -Algorithm SHA256).Hash){throw 'REFRESH_RUNTIME_SOURCE_MISMATCH'}
    $sha=[string](& git -C $Repo rev-parse HEAD)
    if($LASTEXITCODE -ne 0 -or $sha -notmatch '^[0-9a-f]{40}$'){throw 'REFRESH_REVISION_UNKNOWN'}
    $listing=Invoke-RefreshMcp @{action='list_tools';timeout_seconds=30}
    foreach($tool in @('history.sync_batch','mirror.status','mirror.sync','archive.collect')){
        if($tool -notin $listing.tools){throw 'REFRESH_REQUIRED_TOOL_MISSING'}
    }
    $status=(Invoke-RefreshMcp @{action='call';tool='mirror.status';args=@{};timeout_seconds=30}).result
    if($status.source_sha -ne $sha){throw 'REFRESH_SERVER_REVISION_MISMATCH'}
    if($status.enabled -ne $true -or $status.configured_in_process -ne $true -or $status.credentials_file_present -ne $true -or $status.single_writer_declared -ne $true){throw 'REFRESH_MIRROR_NOT_READY'}
    # This proves configuration, never actual Google authorisation or candle coverage.
}

function Invoke-HistoryWindow {
    param([string]$Timeframe,[int]$LookbackDays,[DateTime]$Till)
    $from=$Till.AddDays(-$LookbackDays)
    $payload=Invoke-RefreshMcp @{action='call';tool='history.sync_batch';args=@{
        symbols=@('SBER','Si','BR','GOLD','IMOEX','RI');timeframes=@($Timeframe)
        from_date=$from.ToString('yyyy-MM-dd');till_date=$Till.ToString('yyyy-MM-dd')
    };timeout_seconds=1800}
    $r=$payload.result
    if($r.status -ne 'PASS' -or $r.requested -ne 6 -or $r.passed -ne 6 -or $r.failed -ne 0){throw 'REFRESH_SOURCE_INCOMPLETE'}
    Write-RefreshLog ("REFRESH_PASS timeframe=$Timeframe from=$($from.ToString('yyyy-MM-dd')) till=$($Till.ToString('yyyy-MM-dd')) requested=6 passed=6 failed=0")
}

function Assert-MirrorResult {
    param([string]$Symbol,[object]$Result)
    # RI mirror.sync delegates to archive.update; its readback receipt differs.
    $expected=if($Symbol -eq 'RI'){'MODEL3_ARCHIVE_READBACK_PASS'}else{'MIRROR_SYNC_PASS'}
    if($Result.status -ne $expected){throw 'REFRESH_MIRROR_READBACK_NOT_PROVEN'}
    if($Symbol -eq 'RI'){
        if($Result.written -ne $true){throw 'REFRESH_MIRROR_READBACK_NOT_PROVEN'}
    }else{
        if($Result.symbol -ne $Symbol -or $Result.source_backend -ne 'DuckDB'){throw 'REFRESH_MIRROR_IDENTITY_MISMATCH'}
        foreach($tf in @('D1','H1','M15')){
            if($Result.sheets.$tf.sha256 -notmatch '^[0-9a-f]{64}$'){throw 'REFRESH_MIRROR_READBACK_NOT_PROVEN'}
        }
    }
}

function Invoke-RefreshCycle {
    Assert-RefreshReadiness
    $till=Get-SafeTill
    Write-RefreshLog ("CYCLE_START safe_till=$($till.ToString('yyyy-MM-dd'))")
    Invoke-HistoryWindow -Timeframe 'D1' -LookbackDays 120 -Till $till
    Invoke-HistoryWindow -Timeframe 'H1' -LookbackDays 95 -Till $till
    Invoke-HistoryWindow -Timeframe 'M15' -LookbackDays 35 -Till $till
    Write-RefreshLog ("SOURCE_CYCLE_PASS safe_till=$($till.ToString('yyyy-MM-dd'))")
    foreach($symbol in @('SBER','Si','BR','GOLD','IMOEX','RI')){
        $result=(Invoke-RefreshMcp @{action='call';tool='mirror.sync';args=@{
            symbol=$symbol;from_date=$till.AddDays(-120).ToString('yyyy-MM-dd')
            till_date=$till.ToString('yyyy-MM-dd');dry_run=$false
        };timeout_seconds=1800}).result
        Assert-MirrorResult -Symbol $symbol -Result $result
        Write-RefreshLog ("MIRROR_PASS symbol=$symbol safe_till=$($till.ToString('yyyy-MM-dd'))")
    }
    Write-RefreshLog ("CYCLE_PASS source_and_six_mirrors_verified safe_till=$($till.ToString('yyyy-MM-dd'))")
}

if($env:COMPUTERNAME -ne $ExpectedComputer){
    throw "Birzha history refresh is HOME-only. Current computer=$env:COMPUTERNAME"
}
$currentUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
if(-not $currentUser.EndsWith($ExpectedUserSuffix,[StringComparison]::OrdinalIgnoreCase)){
    throw "Birzha history refresh must run as HOME user. Current user=$currentUser"
}
if($IntervalSeconds -lt 900){throw 'IntervalSeconds must be >= 900'}
if($InitialDelaySeconds -lt 0){throw 'InitialDelaySeconds must be >= 0'}
foreach($path in @($QueueRoot,$TempRoot,$LogRoot)){New-Item -ItemType Directory -Force -Path $path | Out-Null}
if(-not(Test-Path -LiteralPath $VenvPython -PathType Leaf)){throw "Birzha Python missing: $VenvPython"}
if(-not(Test-Path -LiteralPath $McpHelper -PathType Leaf)){throw "MCP helper missing: $McpHelper"}

# A PID file is diagnostic only: Windows can reuse a dead process ID.
$refreshLease = Enter-RefreshLease
if($null -eq $refreshLease){
    Write-RefreshLog 'ALREADY_RUNNING lease_owned=true'
    exit 0
}

try {
    $PID | Set-Content -LiteralPath $PidPath -Encoding ASCII
    Write-RefreshLog ("START pid=$PID user=$currentUser interval_seconds=$IntervalSeconds")
    if($InitialDelaySeconds -gt 0){Start-Sleep -Seconds $InitialDelaySeconds}
    while($true){
        try {
            Invoke-RefreshCycle
        }
        catch {
            Write-RefreshLog 'CYCLE_ERROR check_readiness_or_operation_result' # never log arbitrary upstream text
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}
finally {
    try {
        if(Test-Path -LiteralPath $PidPath -PathType Leaf){
            $current=[int](Get-Content -LiteralPath $PidPath -Raw).Trim()
            if($current -eq $PID){Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue}
        }
    } catch {}
    $refreshLease.ReleaseMutex()
    $refreshLease.Dispose()
}
