"""Offline regressions for the 2026-10-11 independent audit. No MOEX calls."""
from datetime import datetime, timezone
from types import SimpleNamespace
import httpx
import pytest
from birzha.providers.moex_iss import MoexIssClient
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.upstream.safety import SafeRequestExecutor, UpstreamRateLimited
from scripts.g2_capture_and_issue_atomic_staging import _safe_failure_diagnostic
from test_upstream_safety import FakeClock, FakeResponse, executor_for
from birzha.upstream.moex import MOEX_ISS_PUBLIC_POLICY

@pytest.mark.parametrize("header", ["retry-after", "rEtRy-AfTeR", "Retry-After"])
def test_retry_after_case_insensitive(header):
    clock=FakeClock()
    executor=executor_for(MOEX_ISS_PUBLIC_POLICY, clock)
    responses=iter([FakeResponse(503,{header:"12"}),FakeResponse(200)])
    executor.run(lambda:next(responses))
    assert clock.now == 12

def test_retry_after_http_date():
    response=FakeResponse(503,{"retry-after":"Sun, 11 Oct 2026 12:00:20 GMT"})
    executor=SafeRequestExecutor(MOEX_ISS_PUBLIC_POLICY, wall_clock=lambda:datetime(2026,10,11,12,tzinfo=timezone.utc))
    assert executor._retry_after_seconds(response)==20

@pytest.mark.parametrize("value", ["inf", "Infinity", "NaN", "-1"])
def test_invalid_retry_after_never_reaches_sleep(value):
    executor=executor_for(MOEX_ISS_PUBLIC_POLICY,FakeClock())
    assert executor._retry_after_seconds(FakeResponse(503,{"Retry-After":value})) is None

@pytest.mark.parametrize("kind,expected", [(429,"HTTP_429_RATE_LIMIT"),(503,"HTTP_503_UNAVAILABLE"),(502,"HTTP_5XX_UPSTREAM"),("timeout","UPSTREAM_TIMEOUT_OR_TRANSPORT")])
def test_real_client_governor_failure_reaches_safe_diagnostic(kind,expected):
    clock=FakeClock()
    control=ProcessUpstreamControlPlane(clock=clock,sleeper=clock.sleep)
    provider=MoexIssClient(control_plane=control)
    provider._http_client.close()
    attempts=[]
    def get(url):
        attempts.append(url)
        if kind=="timeout": raise httpx.ReadTimeout("secret token https://private.invalid")
        return httpx.Response(kind,headers={"Retry-After":"0"},content=b"private body")
    provider._http_client=SimpleNamespace(get=get)
    with pytest.raises(UpstreamRateLimited) as caught:
        provider._request("/synthetic-only")
    assert len(attempts)==3
    error=caught.value
    row={"error_type":type(error).__name__,"detail":str(error),"safe_reason":getattr(error,"safe_reason",None)}
    assert _safe_failure_diagnostic([row])==expected
    assert "secret" not in str(error) and "private" not in str(error)

@pytest.mark.parametrize("tf", ["D1", "H1", "M1"])
def test_old_page_cannot_hide_behind_fresh_last_receipt(tmp_path, tf):
    import json
    from test_g2_sourcebound_atomic_issuer import stage
    from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
    from test_g2_freeze_real_forecast_from_source_receipts import NOW
    src,root=stage(tmp_path)
    manifest=json.loads((src/"manifest.json").read_bytes())
    page=manifest["instruments"][0]["timeframes"][tf]["pages"][0]
    page["observed_start_utc"]="2026-10-10T14:50:00Z"
    page["observed_end_utc"]="2026-10-10T14:50:05Z"
    (src/"manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match="stale source page"):
        issue_atomic_staging(src,root,"SBER",clock=lambda:NOW)
    assert not list(root.glob("*.duckdb"))

def test_capture_preserves_safe_governor_code_and_never_echoes_unknown_detail(tmp_path):
    from birzha.domain.market import Instrument
    from scripts.g2_six_market_live_source_capture import capture_all
    from test_g2_freeze_real_forecast_from_source_receipts import NOW
    instrument=Instrument(symbol="SBER",secid="SBER",engine="stock",market="shares",board="TQBR",asset_class="equity")
    def refuse(*args):
        raise UpstreamRateLimited("private diagnostic",safe_reason="HTTP_429_RATE_LIMIT")
    manifest=capture_all(SimpleNamespace(_request=refuse),SimpleNamespace(resolve=lambda _:instrument),tmp_path/"raw",clock=lambda:NOW,markets=("SBER",))
    assert _safe_failure_diagnostic(manifest["failed_markets"])=="HTTP_429_RATE_LIMIT"
    assert _safe_failure_diagnostic([{"safe_reason":"https://secret.invalid/token"}])=="UNCLASSIFIED_SOURCE_FAILURE"
    assert _safe_failure_diagnostic([{"error_type":"UpstreamRequestBudgetExceeded"}])=="UPSTREAM_GOVERNOR_BLOCKED"


def test_freshness_rechecked_at_actual_issue_time(tmp_path):
    from datetime import timedelta
    from test_g2_sourcebound_atomic_issuer import stage
    from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
    from test_g2_freeze_real_forecast_from_source_receipts import NOW
    import json
    src,root=stage(tmp_path)
    manifest=json.loads((src/"manifest.json").read_bytes())
    page=manifest["instruments"][0]["timeframes"]["D1"]["pages"][0]
    page["observed_start_utc"]="2026-10-10T14:55:20Z"
    page["observed_end_utc"]="2026-10-10T14:55:21Z"
    (src/"manifest.json").write_text(json.dumps(manifest))
    moments=iter([NOW,NOW+timedelta(seconds=2)])
    with pytest.raises(ValueError,match="stale source page"):
        issue_atomic_staging(src,root,"SBER",clock=lambda:next(moments))
    assert not list(root.glob("*.duckdb"))
