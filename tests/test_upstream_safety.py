from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from birzha.application.upstream_control import (
    ProcessUpstreamControlPlane,
    UpstreamRequestGovernor,
)
from birzha.upstream.moex import (
    MOEX_AUTHENTICATED_POLICY,
    MOEX_ISS_PUBLIC_POLICY,
    policy_for_moex,
)
from birzha.upstream.safety import (
    ProcessPacingGate,
    SafeRequestExecutor,
    UnsafeUpstreamConfiguration,
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


class FakeDistributedGate(ProcessPacingGate):
    scope = "distributed"


def executor_for(policy: UpstreamPolicy, clock: FakeClock, *, distributed: bool = False) -> SafeRequestExecutor:
    gate_cls = FakeDistributedGate if distributed else ProcessPacingGate
    gate = gate_cls(clock=clock, sleeper=clock.sleep)
    return SafeRequestExecutor(policy, gate=gate, sleeper=clock.sleep, jitter=lambda: 0.0)


def test_moex_default_policies_reserve_ten_percent_headroom() -> None:
    assert MOEX_ISS_PUBLIC_POLICY.hard_requests_per_second == 2.0
    assert MOEX_ISS_PUBLIC_POLICY.target_requests_per_second == pytest.approx(1.8)
    assert MOEX_ISS_PUBLIC_POLICY.effective_min_interval_seconds == pytest.approx(0.5 / 0.9)
    assert MOEX_AUTHENTICATED_POLICY.hard_requests_per_second == 1.0
    assert MOEX_AUTHENTICATED_POLICY.target_requests_per_second == pytest.approx(0.9)
    assert MOEX_AUTHENTICATED_POLICY.effective_min_interval_seconds == pytest.approx(1.0 / 0.9)
    assert policy_for_moex(authenticated=False) is MOEX_ISS_PUBLIC_POLICY
    assert policy_for_moex(authenticated=True) is MOEX_AUTHENTICATED_POLICY


def test_executor_spaces_requests_at_effective_not_hard_ceiling() -> None:
    clock = FakeClock()
    policy = UpstreamPolicy(min_interval_seconds=0.5, max_requests_per_operation=10)
    executor = executor_for(policy, clock)
    assert executor.run(lambda: FakeResponse(200)).status_code == 200
    assert executor.run(lambda: FakeResponse(200)).status_code == 200
    assert clock.sleeps == [pytest.approx(0.5 / 0.9)]
    assert executor.requests_used == 2


def test_retry_after_is_honored_and_retry_counts_toward_budget() -> None:
    clock = FakeClock()
    responses = iter([FakeResponse(429, {"Retry-After": "3"}), FakeResponse(200)])
    executor = executor_for(UpstreamPolicy(min_interval_seconds=0.5, max_requests_per_operation=5, max_retries=2, base_backoff_seconds=1.0, max_backoff_seconds=30.0), clock)
    result = executor.run(lambda: next(responses))
    assert result.status_code == 200
    assert executor.requests_used == 2
    assert 3.0 in clock.sleeps


def test_budget_stops_runaway_request_loop() -> None:
    clock = FakeClock()
    executor = executor_for(UpstreamPolicy(min_interval_seconds=0.5, max_requests_per_operation=3, max_retries=2), clock)
    with pytest.raises(UpstreamRateLimited):
        executor.run(lambda: FakeResponse(503))
    with pytest.raises(UpstreamRequestBudgetExceeded):
        executor.run(lambda: FakeResponse(200))


def test_repeated_429_fails_closed_after_bounded_retries() -> None:
    clock = FakeClock()
    executor = executor_for(UpstreamPolicy(min_interval_seconds=0.5, max_requests_per_operation=10, max_retries=2, base_backoff_seconds=1.0, max_backoff_seconds=10.0), clock)
    with pytest.raises(UpstreamRateLimited):
        executor.run(lambda: FakeResponse(429))
    assert executor.requests_used == 3


def test_large_command_is_split_by_worst_case_retry_budget() -> None:
    clock = FakeClock()
    policy = MOEX_ISS_PUBLIC_POLICY
    executor = executor_for(policy, clock)
    governor = UpstreamRequestGovernor(policy, executor, clock=clock, sleeper=clock.sleep)
    plan = governor.plan(20)
    assert plan.soft_attempts_per_window == 18
    assert plan.max_logical_requests_per_batch == 6
    assert [batch.size for batch in plan.batches] == [6, 6, 6, 2]
    assert all(batch.worst_case_attempts <= 18 for batch in plan.batches)


def test_large_command_continues_automatically_with_window_waits() -> None:
    clock = FakeClock()
    policy = UpstreamPolicy(min_interval_seconds=1.0, max_requests_per_operation=30, max_retries=0, target_utilization=0.9, batch_window_seconds=10.0)
    executor = executor_for(policy, clock)
    governor = UpstreamRequestGovernor(policy, executor, clock=clock, sleeper=clock.sleep)
    results = governor.execute(list(range(20)), lambda _: (lambda: FakeResponse(200)))
    assert len(results) == 20
    assert governor.plan(20).batch_count == 3
    assert clock.now >= 20.0
    assert executor.total_requests_used == 20


def test_control_plane_shares_one_gate_for_same_provider() -> None:
    clock = FakeClock()
    policy = UpstreamPolicy(min_interval_seconds=1.0, max_requests_per_operation=10, max_retries=0, target_utilization=0.9)
    control = ProcessUpstreamControlPlane(clock=clock, sleeper=clock.sleep)
    first = control.governor("moex-public", policy)
    second = control.governor("moex-public", policy)
    first.execute([1], lambda _: (lambda: FakeResponse(200)))
    second.execute([2], lambda _: (lambda: FakeResponse(200)))
    assert clock.now == pytest.approx(1.0 / 0.9)


def test_different_provider_keys_do_not_share_gate() -> None:
    clock = FakeClock()
    policy = UpstreamPolicy(min_interval_seconds=1.0, max_requests_per_operation=10, max_retries=0, target_utilization=0.9)
    control = ProcessUpstreamControlPlane(clock=clock, sleeper=clock.sleep)
    first = control.governor("provider-a", policy)
    second = control.governor("provider-b", policy)
    first.execute([1], lambda _: (lambda: FakeResponse(200)))
    second.execute([2], lambda _: (lambda: FakeResponse(200)))
    assert clock.now == 0.0


def test_remote_multi_instance_mode_fails_closed_without_distributed_gate() -> None:
    clock = FakeClock()
    policy = MOEX_ISS_PUBLIC_POLICY
    control = ProcessUpstreamControlPlane(clock=clock, sleeper=clock.sleep)
    with pytest.raises(UnsafeUpstreamConfiguration):
        control.governor("moex-public", policy, require_distributed_gate=True)


def test_remote_mode_accepts_distributed_gate_contract() -> None:
    clock = FakeClock()
    policy = MOEX_ISS_PUBLIC_POLICY
    control = ProcessUpstreamControlPlane(
        gate_factory=lambda _provider_key: FakeDistributedGate(clock=clock, sleeper=clock.sleep),
        clock=clock,
        sleeper=clock.sleep,
    )
    governor = control.governor("moex-public", policy, require_distributed_gate=True)
    assert governor.plan(1).batch_count == 1


def test_control_plane_level_distributed_requirement_fails_closed() -> None:
    clock = FakeClock()
    control = ProcessUpstreamControlPlane(require_distributed_gate=True, clock=clock, sleeper=clock.sleep)
    with pytest.raises(UnsafeUpstreamConfiguration):
        control.governor("moex-public", MOEX_ISS_PUBLIC_POLICY)
