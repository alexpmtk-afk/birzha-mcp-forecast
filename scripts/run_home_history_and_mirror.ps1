param(
    [string]$QueueRoot = 'C:\ProgramData\ChatGPT-PK\BirzhaControlQueue',
    [int]$IntervalSeconds = 3600,
    [int]$InitialDelaySeconds = 45
)

$ErrorActionPreference = 'Stop'

$ExpectedComputer = 'DESKTOP-7F6KPIL'
$ExpectedUserSuffix = '\Win10_Game_OS'
$Repo = 'C:\MCP-HOME\projects\birzha-mcp-forecast'
$VenvPython = Join-Path $Repo '.venv\Scripts\python.exe'
$PolicyRoot = Split-Path -Parent $PSCommandPath
$McpHelper = Join-Path $PolicyRoot 'birzha_mcp_client.py'
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
    param([string]$Name = 'Local\BirzhaHistoryAndMirrorRefresh')
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

function Invoke-HistoryWindow {
    param(
        [string]$Timeframe,
        [int]$LookbackDays,
        [DateTime]$Till
    )
    $from=$Till.AddDays(-$LookbackDays)
    $request=[ordered]@{
        action='call'
        tool='history.sync_batch'
        args=[ordered]@{
            symbols=@('SBER','Si','BR','GOLD','IMOEX','RTSI')
            timeframes=@($Timeframe)
            from_date=$from.ToString('yyyy-MM-dd')
            till_date=$Till.ToString('yyyy-MM-dd')
        }
        timeout_seconds=1800
    }
    $requestPath=Join-Path $TempRoot ('history-refresh-'+$Timeframe+'-'+[Guid]::NewGuid().ToString('N')+'.json')
    try {
        $request | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $requestPath -Encoding UTF8
        $output=@(& $VenvPython $McpHelper --request $requestPath 2>&1)
        $exitCode=$LASTEXITCODE
        $last=@($output | Where-Object {-not [string]::IsNullOrWhiteSpace([string]$_)} | Select-Object -Last 1)
        if($last.Count -eq 0){
            throw "history refresh helper returned no JSON for $Timeframe"
        }
        $payload=([string]$last[0]) | ConvertFrom-Json
        $toolStatus=[string]$payload.result.status
        $requested=[int]($payload.result.requested)
        $passed=[int]($payload.result.passed)
        $failed=[int]($payload.result.failed)
        if($exitCode -ne 0 -or [string]$payload.status -ne 'PASS' -or $toolStatus -ne 'PASS'){
            throw ("history refresh $Timeframe failed exit=$exitCode wrapper=$([string]$payload.status) tool=$toolStatus requested=$requested passed=$passed failed=$failed")
        }
        Write-RefreshLog ("REFRESH_PASS timeframe=$Timeframe from=$($from.ToString('yyyy-MM-dd')) till=$($Till.ToString('yyyy-MM-dd')) requested=$requested passed=$passed failed=$failed")
    }
    finally {
        Remove-Item -LiteralPath $requestPath -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-RefreshCycle {
    $till=Get-SafeTill
    Write-RefreshLog ("CYCLE_START safe_till=$($till.ToString('yyyy-MM-dd'))")
    Invoke-HistoryWindow -Timeframe 'D1' -LookbackDays 120 -Till $till
    Invoke-HistoryWindow -Timeframe 'H1' -LookbackDays 95 -Till $till
    Invoke-HistoryWindow -Timeframe 'M15' -LookbackDays 35 -Till $till
    # Source refresh success is separate from verified Google mirror success.
    Write-RefreshLog ("SOURCE_CYCLE_PASS safe_till=$($till.ToString('yyyy-MM-dd'))")
    foreach($symbol in @('SBER','Si','BR','GOLD','IMOEX','RTSI')){
        $mirrorRequest=[ordered]@{
            action='call'; tool='mirror.sync'; args=[ordered]@{
                symbol=$symbol; from_date=$till.AddDays(-120).ToString('yyyy-MM-dd');
                till_date=$till.ToString('yyyy-MM-dd'); dry_run=$false
            }; timeout_seconds=1800
        }
        $mirrorRequestPath=Join-Path $TempRoot ('mirror-refresh-'+[Guid]::NewGuid().ToString('N')+'.json')
        try {
            $mirrorRequest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $mirrorRequestPath -Encoding UTF8
            $mirrorOutput=@(& $VenvPython $McpHelper --request $mirrorRequestPath 2>&1)
            $mirrorExit=$LASTEXITCODE
            $mirrorLast=@($mirrorOutput | Where-Object {-not [string]::IsNullOrWhiteSpace([string]$_)} | Select-Object -Last 1)
            if($mirrorLast.Count -eq 0){throw 'Mirror helper returned no structured result'}
            $mirrorResult=([string]$mirrorLast[0]) | ConvertFrom-Json
            if($mirrorExit -ne 0 -or [string]$mirrorResult.status -ne 'PASS' -or [string]$mirrorResult.result.status -ne 'MIRROR_SYNC_PASS'){
                throw ("Mirror did not pass symbol=$symbol helper_exit=$mirrorExit wrapper_status=$([string]$mirrorResult.status) mirror_status=$([string]$mirrorResult.result.status)")
            }
            Write-RefreshLog ("MIRROR_PASS symbol=$symbol safe_till=$($till.ToString('yyyy-MM-dd'))")
        }
        finally {Remove-Item -LiteralPath $mirrorRequestPath -Force -ErrorAction SilentlyContinue}
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
            Write-RefreshLog ("CYCLE_ERROR type=$($_.Exception.GetType().Name) message=$($_.Exception.Message)")
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
