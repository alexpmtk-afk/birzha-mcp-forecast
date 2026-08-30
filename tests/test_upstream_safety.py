from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from birzha.upstream.moex import (
    MOEX_AUTHENTICATED_POLICY,
    MOEX_ISS_PUBLIC_POLICY,
    policy_for_moex,
)
from birzha.upstream.safety import (
    SafeRequestExecutor,
    UpstreamPolicy,
    UpstreamRateLimited,
    UpstreamRequestBudgetExceeded,
)


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


def test_moex_default_policies_are_conservative() -> None:
    assert MOEX_ISS_PUBLIC_POLICY.min_interval_seconds == 0.5
    assert MOEX_AUTHENTICATED_POLICY.min_interval_seconds == 1.0
    assert policy_for_moex(authenticated=False) is MOEX_ISS_PUBLIC_POLICY
    assert policy_for_moex(authenticated=True) is MOEX_AUTHENTICATED_POLICY


def test_executor_spaces_requests() -> None:
    clock = FakeClock()
    executor = SafeRequestExecutor(
        UpstreamPolicy(min_interval_seconds=0.5, max_requests_per_operation=10),
        clock=clock,
        sleeper=clock.sleep,
        jitter=lambda: 0.0,
    )

    assert executor.run(lambda: FakeResponse(200)).status_code == 200
    assert executor.run(lambda: FakeResponse(200)).status_code == 200
    assert clock.sleeps == [0.5]
    assert executor.requests_used == 2


def test_retry_after_is_honored_and_retry_counts_toward_budget() -> None:
    clock = FakeClock()
    responses = iter([FakeResponse(429, {"Retry-After": "3"}), FakeResponse(200)])
    executor = SafeRequestExecutor(
        UpstreamPolicy(
            min_interval_seconds=0.5,
            max_requests_per_operation=5,
            max_retries=2,
            base_backoff_seconds=1.0,
            max_backoff_seconds=30.0,
        ),
        clock=clock,
        sleeper=clock.sleep,
        jitter=lambda: 0.0,
    )

    result = executor.run(lambda: next(responses))

    assert result.status_code == 200
    assert executor.requests_used == 2
    assert 3.0 in clock.sleeps


def test_budget_stops_runaway_request_loop() -> None:
    clock = FakeClock()
    executor = SafeRequestExecutor(
        UpstreamPolicy(
            min_interval_seconds=0.5,
            max_requests_per_operation=1,
            max_retries=2,
        ),
        clock=clock,
        sleeper=clock.sleep,
        jitter=lambda: 0.0,
    )

    with pytest.raises(UpstreamRequestBudgetExceeded):
        executor.run(lambda: FakeResponse(503))


def test_repeated_429_fails_closed_after_bounded_retries() -> None:
    clock = FakeClock()
    executor = SafeRequestExecutor(
        UpstreamPolicy(
            min_interval_seconds=0.5,
            max_requests_per_operation=10,
            max_retries=2,
            base_backoff_seconds=1.0,
            max_backoff_seconds=10.0,
        ),
        clock=clock,
        sleeper=clock.sleep,
        jitter=lambda: 0.0,
    )

    with pytest.raises(UpstreamRateLimited):
        executor.run(lambda: FakeResponse(429))

    assert executor.requests_used == 3
