param([int]$IntervalSeconds=3600,[string]$Timeframes='D1',[switch]$Apply,[switch]$Once)
$ErrorActionPreference='Stop'
# Repository-owned script. No temp request files, PID journal, history.sync, mirror.sync or DuckDB.
if($IntervalSeconds -lt 900){throw 'ARCHIVE_INTERVAL_TOO_SHORT'}
if(-not $env:BIRZHA_SOURCE_SHA -or -not $env:BIRZHA_MCP_BEARER_TOKEN){throw 'EXPLICIT_ARCHIVE_SHA_AUTH_REQUIRED'}
$repo=Split-Path -Parent $PSScriptRoot
$python=Join-Path $repo '.venv\Scripts\python.exe'
function Enter-RefreshLease {
    param([string]$Name = 'Global\BirzhaGoogleFirstArchiveCycle')
    $lease = [Threading.Mutex]::new($false, $Name)
    try {
        try { $acquired = $lease.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if(-not $acquired){ $lease.Dispose(); return $null }
        return ,$lease
    }
    catch { $lease.Dispose(); throw }
}

$refreshLease = Enter-RefreshLease
if($null -eq $refreshLease){throw 'ARCHIVE_CYCLE_ALREADY_RUNNING'}
try{
 do{
  $arguments=@((Join-Path $PSScriptRoot 'run_google_archive_cycle.py'),'--timeframes',$Timeframes)
  if($Apply){$arguments+='--apply'}
  & $python @arguments
  if($Once -and $LASTEXITCODE -ne 0){throw 'ARCHIVE_CYCLE_PARTIAL_CHECK_GOOGLE_STATUS'}
  if(-not $Once){Start-Sleep -Seconds $IntervalSeconds}
 }while(-not $Once)
}finally{$refreshLease.ReleaseMutex();$refreshLease.Dispose()}
