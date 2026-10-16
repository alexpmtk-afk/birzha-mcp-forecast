"""Exercise the real PowerShell lease without starting HOME or network work."""
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_home_history_and_mirror.ps1"
SHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


def test_refresh_uses_os_lease_instead_of_pid_liveness():
    source = SCRIPT.read_text(encoding="utf-8-sig")
    assert "Get-Process -Id $existingPid" not in source
    assert "$refreshLease = Enter-RefreshLease" in source
    assert source.index("$refreshLease = Enter-RefreshLease") < source.index("$PID | Set-Content")
    assert "AbandonedMutexException" in source
    assert "$refreshLease.ReleaseMutex()" in source
    assert "$refreshLease.Dispose()" in source


@pytest.mark.skipif(not SHELL, reason="PowerShell runtime required")
@pytest.mark.parametrize("abandon", [False, True])
def test_real_lease_rejects_concurrent_owner_and_allows_restart(tmp_path, abandon):
    harness = tmp_path / "lease.ps1"
    # Parse the reviewed script; execute only its lease function, never its body.
    script_path = str(SCRIPT).replace("'", "''")
    harness.write_text("""param([string]$Name,[switch]$Hold)
$ErrorActionPreference='Stop'
$tokens=$null; $parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('""" + script_path + """',[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'Script parse failed'}
$fn=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Enter-RefreshLease'},$true)
Invoke-Expression $fn.Extent.Text
$lease=Enter-RefreshLease -Name $Name
if($null -eq $lease){[Console]::WriteLine('BUSY');exit 0}
[Console]::WriteLine('ACQUIRED')
if($Hold){[Console]::ReadLine() | Out-Null}
$lease.ReleaseMutex();$lease.Dispose()
""", encoding="utf-8-sig")
    name = "Local\\BirzhaRefreshTest-" + uuid.uuid4().hex
    command = [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness), "-Name", name]
    holder = subprocess.Popen([*command, "-Hold"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ACQUIRED"
        blocked = subprocess.run(command, capture_output=True, text=True, timeout=20)
        assert blocked.returncode == 0 and blocked.stdout.strip() == "BUSY", blocked.stderr
        if abandon:
            holder.terminate()
            holder.wait(timeout=10)
        else:
            holder.communicate("release\n", timeout=20)
            assert holder.returncode == 0
        restarted = subprocess.run(command, capture_output=True, text=True, timeout=20)
        assert restarted.returncode == 0 and restarted.stdout.strip() == "ACQUIRED", restarted.stderr
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=10)


@pytest.mark.skipif(not SHELL, reason="PowerShell runtime required")
@pytest.mark.parametrize("pinned",[None,"a"*40,"b"*40])
def test_startup_revision_guard_survives_existing_controller_assignment_rewrite(pinned):
    source=(SCRIPT.parent/"start_home_with_native_mirror.ps1").read_text(encoding="utf-8-sig")
    snippet=source[source.index("$installedSourceSha ="):source.index("$env:MCP_ALLOWED_HOSTS")]
    if pinned:
        snippet=snippet.replace("$env:BIRZHA_SOURCE_SHA = $installedSourceSha", "$env:BIRZHA_SOURCE_SHA = '"+pinned+"'")
    # Stub git in a child process; do not execute launcher/network/secret reads.
    stub="$ErrorActionPreference='Stop';$repo='test-only';function git { $global:LASTEXITCODE=0; '"+"a"*40+"' };"
    result=subprocess.run([SHELL,"-NoProfile","-NonInteractive","-Command",stub+snippet],capture_output=True,text=True,timeout=20)
    assert (result.returncode==0)==(pinned in (None,"a"*40)),result.stderr
