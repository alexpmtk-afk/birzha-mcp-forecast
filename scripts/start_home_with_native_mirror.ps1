$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'DESKTOP-7F6KPIL') { throw 'HOME host mismatch' }
$repo = 'C:\MCP-HOME\projects\birzha-mcp-forecast'
$env:HOST = '127.0.0.1'
$env:PORT = '18082'
$env:BIRZHA_STATE_BACKEND = 'duckdb'
$env:BIRZHA_FORECAST_JOURNAL_PATH = 'C:\MCP-HOME\data\birzha\birzha_state.duckdb'
$env:BIRZHA_HISTORICAL_STORE_PATH = 'C:\MCP-HOME\data\birzha\birzha_history.duckdb'
$env:BIRZHA_DURABLE_LOCAL_ORCHESTRATION = 'true'
$env:BIRZHA_SOURCE_SHA = [string](& git -C $repo rev-parse HEAD)
if($LASTEXITCODE -ne 0 -or $env:BIRZHA_SOURCE_SHA -notmatch '^[0-9a-f]{40}$'){throw 'Installed source revision unavailable'}
$env:MCP_ALLOWED_HOSTS = '127.0.0.1,127.0.0.1:*,localhost,localhost:*'
$env:MCP_ALLOWED_ORIGINS = ''
$env:BIRZHA_REQUIRE_MCP_AUTH = 'true'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$auth = Get-Content -LiteralPath 'C:\MCP-HOME\secrets\birzha\birzha-mcp-forecast-test-auth.json' -Raw | ConvertFrom-Json
$env:BIRZHA_MCP_BEARER_TOKEN = [string]($auth.entries | Where-Object key -eq 'bearer_token').text_value
if (-not $env:BIRZHA_MCP_BEARER_TOKEN) { throw 'Missing local bearer token' }
$bridge = Get-Content -LiteralPath 'C:\MCP-HOME\secrets\birzha\birzha-mcp-forecast-drive-bridge-v1.json' -Raw | ConvertFrom-Json
$bridgeConfig = 'C:\MCP-HOME\runtime\birzha\bridge-config.json'
$env:BIRZHA_MARKET_MIRROR_REQUIRED = 'false'
$env:BIRZHA_MARKET_MIRROR_BRIDGE_URL = ''
$env:BIRZHA_MARKET_MIRROR_BRIDGE_SECRET = ''
$env:BIRZHA_MARKET_MIRROR_ROOT_FOLDER_ID = ''
if (Test-Path -LiteralPath $bridgeConfig) {
    $cfg = Get-Content -LiteralPath $bridgeConfig -Raw | ConvertFrom-Json
    if (-not $cfg.url -or -not $cfg.root_folder_id) { throw 'Incomplete bridge config' }
    $env:BIRZHA_MARKET_MIRROR_BRIDGE_URL = [string]$cfg.url
    $env:BIRZHA_MARKET_MIRROR_ROOT_FOLDER_ID = [string]$cfg.root_folder_id
    $env:BIRZHA_MARKET_MIRROR_BRIDGE_SECRET = [string]($bridge.entries | Where-Object key -eq 'gdrive_bridge_v1_secret').text_value
    $env:BIRZHA_MARKET_MIRROR_REQUIRED = 'true'
}
# Explicit native mirror configuration; no automatic discovery of credentials.
$env:BIRZHA_NATIVE_MIRROR_ENABLED = 'false'
$env:BIRZHA_NATIVE_MIRROR_CREDENTIALS_PATH = ''
$env:BIRZHA_NATIVE_MIRROR_SOLE_WRITER = 'false'
$nativeConfigPath = 'C:\MCP-HOME\runtime\birzha\native-mirror-config.json'
if(Test-Path -LiteralPath $nativeConfigPath){
    $nativeConfig=Get-Content -LiteralPath $nativeConfigPath -Raw | ConvertFrom-Json
    if($nativeConfig.enabled -ne $true -or $nativeConfig.sole_writer -ne $true -or -not $nativeConfig.credentials_path){throw 'Explicit native mirror activation configuration required'}
    if(Test-Path -LiteralPath $bridgeConfig){throw 'Legacy and native Google writers cannot both be enabled'}
    if(-not(Test-Path -LiteralPath ([string]$nativeConfig.credentials_path) -PathType Leaf)){throw 'Google background authorisation file missing'}
    $env:BIRZHA_NATIVE_MIRROR_ENABLED='true'
    $env:BIRZHA_NATIVE_MIRROR_CREDENTIALS_PATH=[string]$nativeConfig.credentials_path
    $env:BIRZHA_NATIVE_MIRROR_SOLE_WRITER='true'
}
& 'C:\MCP-HOME\projects\birzha-mcp-forecast\.venv\Scripts\python.exe' 'C:\MCP-HOME\runtime\birzha\check-state.py' *> 'C:\MCP-HOME\logs\birzha\startup-state.log'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Set-Location -LiteralPath $repo
$child = Start-Process -FilePath "$repo\.venv\Scripts\python.exe" -ArgumentList @('-m','birzha.main') -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput "C:\MCP-HOME\logs\birzha\stdout-$stamp.log" -RedirectStandardError "C:\MCP-HOME\logs\birzha\stderr-$stamp.log"
$child.Id | Set-Content -LiteralPath 'C:\MCP-HOME\runtime\birzha\server.pid'
$env:BIRZHA_MCP_BEARER_TOKEN = ''
$env:BIRZHA_MARKET_MIRROR_BRIDGE_SECRET = ''
$child.WaitForExit()
exit $child.ExitCode
