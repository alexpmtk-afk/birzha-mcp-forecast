"""Offline strict per-one-capture deadline/attempt testing. No MOEX calls."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.providers.moex_iss import IssResponse, MoexIssClient
from birzha.upstream.capture_scope import (
    CaptureScopeRefused, active_capture_scope, bounded_market_capture,
)
from scripts.g2_capture_and_issue_atomic_staging import (
    _safe_failure_diagnostic, capture_and_issue_one,
)


class FakeMono:
    def __init__(self):
        self.value = 1_000.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def provider(clock, reply=None):
    control = ProcessUpstreamControlPlane(clock=clock, sleeper=clock.sleep)
    client = MoexIssClient(control_plane=control)
    calls = []
    def request(url):
        calls.append(url)
        return reply(url) if reply else IssResponse(
            status_code=200, headers={}, body=b"valid synthetic page"
        )
    client._one_attempt = request
    return client, calls


def test_cumulative_physical_attempts_include_repeated_independent_governed_segments():
    mono = FakeMono()
    client, calls = provider(mono)
    with bounded_market_capture(max_attempts=2, monotonic=mono):
        assert client._request("/fake-resolver").status_code == 200
        assert client._request("/fake-minute-page").status_code == 200
        with pytest.raises(CaptureScopeRefused, match="CAPTURE_TOTAL_ATTEMPTS_EXCEEDED"):
            client._request("/fake-next-page")
        assert len(calls) == 2
    assert active_capture_scope() is None
    # New independent captures must have a fresh scope, not a process-global counter.
    with bounded_market_capture(max_attempts=2, monotonic=mono):
        assert client._request("/fake-new-capture").status_code == 200
    assert len(calls) == 3


def test_retry_after_longer_than_operation_deadline_never_slept_or_retried():
    mono = FakeMono()
    def reply(url):
        return IssResponse(429, {"retry-after": "12"}, b"")
    client, calls = provider(mono, reply)
    with bounded_market_capture(deadline_seconds=5, max_attempts=120, monotonic=mono) as scope:
        with pytest.raises(CaptureScopeRefused, match="CAPTURE_DEADLINE_EXCEEDED"):
            client._request("/fake-429")
        assert scope.physical_attempts == 1
    assert len(calls) == 1
    assert mono.value < 1_005.0


def test_retry_wait_fits_but_second_attempt_exceeds_global_attempt_budget():
    mono = FakeMono()
    client, calls = provider(mono, lambda url: IssResponse(503, {"retry-after": "1"}, b""))
    with bounded_market_capture(max_attempts=1, monotonic=mono):
        with pytest.raises(CaptureScopeRefused, match="CAPTURE_TOTAL_ATTEMPTS_EXCEEDED"):
            client._request("/fake-503")
    assert len(calls) == 1


def test_response_arrives_after_deadline_and_cannot_count_as_success():
    mono = FakeMono()
    def reply(url):
        mono.sleep(2)
        return IssResponse(200, {}, b"")
    client, calls = provider(mono, reply)
    with bounded_market_capture(deadline_seconds=1, monotonic=mono):
        with pytest.raises(CaptureScopeRefused, match="CAPTURE_DEADLINE_EXCEEDED"):
            client._request("/fake-too-slow")
    assert len(calls) == 1


def test_pacing_wait_is_checked_again_before_actual_network_call():
    mono = FakeMono()
    client, calls = provider(mono)
    with bounded_market_capture(deadline_seconds=0.2, monotonic=mono):
        client._request("/fake-first")
        with pytest.raises(CaptureScopeRefused, match="CAPTURE_DEADLINE_EXCEEDED"):
            client._request("/fake-second")
    assert len(calls) == 1


def test_timeout_or_budget_refusal_before_issuer_never_creates_duckdb(tmp_path, monkeypatch):
    from scripts import g2_capture_and_issue_atomic_staging as wrapper
    mono = FakeMono()
    client, calls = provider(mono)
    capture_path = tmp_path / "raw"
    staged = tmp_path / "sandbox"
    staged.mkdir()

    def fake_capture(provider, market_data, out_dir, *, clock, markets):
        provider._request("/fake-resolver")
        provider._request("/fake-M1-page")
        provider._request("/fake-next-page")
        raise AssertionError("unexpected full source capture success")

    def forbidden_issue(*args, **kwargs):
        raise AssertionError("Forecast issuance invoked after exhausted capture scope")

    monkeypatch.setattr(wrapper, "capture_all", fake_capture)
    monkeypatch.setattr(wrapper, "issue_atomic_staging", forbidden_issue)
    with pytest.raises(CaptureScopeRefused, match="CAPTURE_TOTAL_ATTEMPTS_EXCEEDED"):
        capture_and_issue_one(
            client, object(), "SBER", capture_path, staged,
            capture_monotonic=mono, capture_max_attempts=2,
        )
    assert len(calls) == 2
    assert not list(staged.iterdir())
    assert active_capture_scope() is None


def test_no_partial_forecast_if_capture_exhausts_its_monotonic_deadline(tmp_path, monkeypatch):
    from scripts import g2_capture_and_issue_atomic_staging as wrapper
    mono = FakeMono()
    client, calls = provider(mono)
    staged = tmp_path / "sandbox"
    staged.mkdir()

    def fake_capture(provider, market_data, out_dir, *, clock, markets):
        provider._request("/fake-resolve")
        mono.sleep(10)
        return {
            "verified_complete_market_count": 1,
            "failed_markets": [],
            "complete_markets": ["SBER"],
        }

    monkeypatch.setattr(wrapper, "capture_all", fake_capture)
    monkeypatch.setattr(wrapper, "issue_atomic_staging", lambda *a, **kw: pytest.fail(
        "issuer must never be called after capture deadline"
    ))
    with pytest.raises(CaptureScopeRefused, match="CAPTURE_DEADLINE_EXCEEDED"):
        capture_and_issue_one(
            client, object(), "SBER", tmp_path / "raw", staged,
            capture_monotonic=mono, capture_deadline_seconds=5,
        )
    assert len(calls) == 1
    assert not list(staged.iterdir())


def test_context_scopes_are_not_shared_across_concurrent_workers():
    def worker():
        mono = FakeMono()
        client, calls = provider(mono)
        with bounded_market_capture(max_attempts=1, monotonic=mono):
            assert client._request("/fake-only").status_code == 200
            with pytest.raises(CaptureScopeRefused):
                client._request("/fake-second")
        return len(calls), active_capture_scope()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: worker(), (1, 2)))
    assert result == [(1, None), (1, None)]


def test_known_safe_classification_and_scope_validation():
    assert _safe_failure_diagnostic([{
        "safe_reason": "CAPTURE_TOTAL_ATTEMPTS_EXCEEDED",
        "detail": "should-not-appear in reports",
    }]) == "CAPTURE_TOTAL_ATTEMPTS_EXCEEDED"
    assert _safe_failure_diagnostic([{
        "safe_reason": "CAPTURE_DEADLINE_EXCEEDED",
        "detail": "private url https://secret.invalid",
    }]) == "CAPTURE_DEADLINE_EXCEEDED"
    with pytest.raises(ValueError):
        with bounded_market_capture(max_attempts=121):
            pass
    with pytest.raises(ValueError):
        with bounded_market_capture(deadline_seconds=241):
            pass
