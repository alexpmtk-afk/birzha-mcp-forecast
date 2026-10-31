"""Exercise real refresh functions with fake dispatch; never import MCP or open HOME DB."""
from pathlib import Path
import shutil, subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]

def test_refresh_helper_is_external_and_configured_source_uses_ri():
    s=(ROOT/'scripts/run_home_history_and_mirror.ps1').read_text(encoding='utf-8-sig')
    assert "symbols=@('SBER','Si','BR','GOLD','IMOEX','RI')" in s
    assert 'RTSI' not in s
    assert "ChatGPT-PK\\BirzhaControl\\birzha_mcp_client.py" in s
    assert "Global\\BirzhaHistoryAndMirrorRefresh" in s

@pytest.mark.parametrize('case',['cycle','disabled','old-sha','missing-tool','stale-runtime','ri-dry-run','ri-not-written','wrong-symbol'])
def test_refresh_functions_without_real_dispatch(case):
    ps=shutil.which('powershell.exe') or shutil.which('pwsh')
    if not ps: pytest.skip('PowerShell required for actual runtime functions')
    p=subprocess.run([ps,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'tests/home_refresh_fixture.ps1'),'-Source',str(ROOT/'scripts/run_home_history_and_mirror.ps1'),'-Case',case],capture_output=True,text=True,timeout=30)
    assert p.returncode==0, p.stdout+p.stderr
    assert 'FAKE_DISPATCH_ONLY_PASS' in p.stdout
