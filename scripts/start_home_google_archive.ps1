param([Parameter(Mandatory=$true)][string]$GoogleCredentialsPath,[int]$Port=18085,[switch]$OwnerConfirmedSoleWriter,[switch]$CheckOnly)
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
$python=Join-Path $repo '.venv\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $python -PathType Leaf)){throw 'PROJECT_PYTHON_REQUIRED'}
if(-not(Test-Path -LiteralPath $GoogleCredentialsPath -PathType Leaf)){throw 'EXPLICIT_GOOGLE_CREDENTIAL_FILE_REQUIRED'}
if(-not $env:BIRZHA_MCP_BEARER_TOKEN){throw 'ARCHIVE_BEARER_FROM_PROTECTED_ENV_REQUIRED'}
if($Port -lt 1024 -or $Port -gt 65535 -or $Port -in @(18082,18083,18084)){throw 'SEPARATE_LOOPBACK_PORT_REQUIRED'}
if(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue){throw 'ARCHIVE_PORT_ALREADY_USED'}
$sha=[string](& git -C $repo rev-parse HEAD)
if($LASTEXITCODE -ne 0 -or $sha -notmatch '^[0-9a-f]{40}$'){throw 'INSTALLED_ARCHIVE_SHA_REQUIRED'}
if($CheckOnly){Write-Output 'GOOGLE_ARCHIVE_CONFIGURATION_CHECKED_NO_PROCESS_STARTED';return}
$env:BIRZHA_SOURCE_SHA=$sha
$env:BIRZHA_ARCHIVE_GOOGLE_CREDENTIALS_PATH=$GoogleCredentialsPath
$env:BIRZHA_ARCHIVE_PORT=[string]$Port
$env:BIRZHA_ARCHIVE_SOLE_WRITER=if($OwnerConfirmedSoleWriter){'true'}else{'false'}
$env:BIRZHA_NATIVE_MIRROR_ENABLED='false'
$env:PYTHONPATH=Join-Path $repo 'src'
$env:PYTHONDONTWRITEBYTECODE='1'
# No DB paths, checkpoint, migration, local M1 path or permanent state directory.
& $python -m birzha.mcp.archive_server
exit $LASTEXITCODE
