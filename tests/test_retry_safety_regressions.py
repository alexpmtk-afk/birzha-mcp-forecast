from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from birzha.upstream.safety import ProcessPacingGate, SafeRequestExecutor, UpstreamPolicy


@dataclass
class FakeClock:
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class FakeResponse:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)


def _executor(clock: FakeClock) -> SafeRequestExecutor:
    policy = UpstreamPolicy(
        min_interval_seconds=0.5,
        max_requests_per_operation=10,
        max_retries=2,
        base_backoff_seconds=1.0,
        max_backoff_seconds=30.0,
        no_header_429_cooldown_seconds=60.0,
    )
    gate = ProcessPacingGate(clock=clock, sleeper=clock.sleep)
    return SafeRequestExecutor(policy, gate=gate, sleeper=clock.sleep, jitter=lambda: 0.0)


def test_retry_after_larger_than_normal_backoff_ceiling_is_never_shortened() -> None:
    clock = FakeClock()
    executor = _executor(clock)
    responses = iter([FakeResponse(429, {"Retry-After": "120"}), FakeResponse(200)])

    assert executor.run(lambda: next(responses)).status_code == 200
    assert 120.0 in clock.sleeps
    assert 30.0 not in clock.sleeps


def test_429_without_retry_after_uses_conservative_cooldown() -> None:
    clock = FakeClock()
    executor = _executor(clock)
    responses = iter([FakeResponse(429), FakeResponse(200)])

    assert executor.run(lambda: next(responses)).status_code == 200
    assert 60.0 in clock.sleeps


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_and_authorization_failures_are_not_retried(status: int) -> None:
    clock = FakeClock()
    executor = _executor(clock)
    calls = 0

    def request() -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse(status)

    result = executor.run(request)

    assert result.status_code == status
    assert calls == 1
    assert executor.requests_used == 1
