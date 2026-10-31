param([string]$Source,[string]$Case)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse failed'}
$functions=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)
foreach($f in $functions){Invoke-Expression $f.Extent.Text}
# Only function definitions above: no production top-level code is executed.
$script:calls=@();$script:disabled=$false;$script:oldsha=$false;$script:missing=$false;$script:hashMismatch=$false
$Repo='FAKE';$RefreshScriptPath=$Source;$script:sha='a'*40
function Write-RefreshLog {param($Message)}
function Test-Path {param($LiteralPath,$PathType) return $true}
function Get-FileHash {param($LiteralPath,$Algorithm) if($script:hashMismatch -and $LiteralPath -eq $Source){return @{Hash='bad'}};return @{Hash='same'}}
function git {$global:LASTEXITCODE=0;return $script:sha}
function Invoke-RefreshMcp {
 param($Request)
 $script:calls+=,$Request
 if($Request.action -eq 'list_tools'){
  return @{status='PASS';tools=@('mirror.status','mirror.sync','history.sync_batch')+$(if(-not $script:missing){'archive.collect'})}
 }
 if($Request.tool -eq 'mirror.status'){return @{status='PASS';result=@{enabled=(-not $script:disabled);configured_in_process=$true;credentials_file_present=$true;single_writer_declared=$true;source_sha=$(if($script:oldsha){'b'*40}else{$script:sha})}}}
 if($Request.tool -eq 'history.sync_batch'){return @{status='PASS';result=@{status='PASS';requested=6;passed=6;failed=0}}}
 if($Request.tool -eq 'mirror.sync'){
  if($Request.args.symbol -eq 'RI'){return @{status='PASS';result=@{status='MODEL3_ARCHIVE_READBACK_PASS';written=$true}}}
  $s=@{};foreach($tf in @('D1','H1','M15')){$s[$tf]=@{sha256='c'*64}}
  return @{status='PASS';result=@{status='MIRROR_SYNC_PASS';symbol=$Request.args.symbol;source_backend='DuckDB';sheets=$s}}
 }
 throw 'UNEXPECTED_FAKE_REQUEST'
}
function Expect-Fail([scriptblock]$Work,[string]$Code){
 try {& $Work}catch{if($_.Exception.Message -eq $Code){return};throw}
 throw 'expected rejection missing'
}
switch($Case){
 'cycle' {
  Invoke-RefreshCycle
  $history=@($script:calls|Where-Object tool -eq 'history.sync_batch')
  $mirrors=@($script:calls|Where-Object tool -eq 'mirror.sync')
  if($history.Count -ne 3 -or $mirrors.Count -ne 6){throw 'wrong dispatch count'}
  foreach($r in $history){if(($r.args.symbols -join ',') -ne 'SBER,Si,BR,GOLD,IMOEX,RI'){throw 'wrong source universe'}}
  if(($mirrors.args.symbol -join ',') -ne 'SBER,Si,BR,GOLD,IMOEX,RI'){throw 'wrong mirror universe'}
 }
 'disabled' {$script:disabled=$true;Expect-Fail {Invoke-RefreshCycle} 'REFRESH_MIRROR_NOT_READY';if(@($script:calls|Where-Object tool -eq 'history.sync_batch').Count){throw 'source write before readiness'}}
 'old-sha' {$script:oldsha=$true;Expect-Fail {Invoke-RefreshCycle} 'REFRESH_SERVER_REVISION_MISMATCH'}
 'missing-tool' {$script:missing=$true;Expect-Fail {Invoke-RefreshCycle} 'REFRESH_REQUIRED_TOOL_MISSING'}
 'stale-runtime' {$script:hashMismatch=$true;Expect-Fail {Invoke-RefreshCycle} 'REFRESH_RUNTIME_SOURCE_MISMATCH';if($script:calls.Count){throw 'request before runtime validation'}}
 'ri-dry-run' {Expect-Fail {Assert-MirrorResult 'RI' @{status='MODEL3_ARCHIVE_DRY_RUN_READY';written=$false}} 'REFRESH_MIRROR_READBACK_NOT_PROVEN'}
 'ri-not-written' {Expect-Fail {Assert-MirrorResult 'RI' @{status='MODEL3_ARCHIVE_READBACK_PASS';written=$false}} 'REFRESH_MIRROR_READBACK_NOT_PROVEN'}
 'wrong-symbol' {Expect-Fail {Assert-MirrorResult 'Si' @{status='MIRROR_SYNC_PASS';symbol='SBER';source_backend='DuckDB'}} 'REFRESH_MIRROR_IDENTITY_MISMATCH'}
 default {throw 'unknown case'}
}
'FAKE_DISPATCH_ONLY_PASS'
