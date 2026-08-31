"""Upper-level outbound request governor."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Sequence, TypeVar

from birzha.upstream.safety import (
    PacingGate,
    ProcessPacingGate,
    ResponseLike,
    SafeRequestExecutor,
    UnsafeUpstreamConfiguration,
    UpstreamPolicy,
)

TaskT = TypeVar("TaskT")
ResultT = TypeVar("ResultT", bound=ResponseLike)


@dataclass(frozen=True, slots=True)
class RequestBatch:
    index: int
    start: int
    stop: int
    size: int
    worst_case_attempts: int


@dataclass(frozen=True, slots=True)
class RequestPlan:
    total_logical_requests: int
    target_utilization: float
    hard_requests_per_second: float
    target_requests_per_second: float
    window_seconds: float
    soft_attempts_per_window: int
    max_logical_requests_per_batch: int
    batches: tuple[RequestBatch, ...]

    @property
    def batch_count(self) -> int:
        return len(self.batches)


class ProcessUpstreamControlPlane:
    """Top-level owner of shared provider pacing gates.

    Local development uses process-wide gates. Remote YDB runtime injects a
    provider-keyed distributed gate factory and requires distributed scope for
    every outbound provider governor.
    """

    def __init__(
        self,
        *,
        gate_factory: Callable[[str], PacingGate] | None = None,
        require_distributed_gate: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._gate_factory = gate_factory or (
            lambda _provider_key: ProcessPacingGate(clock=self._clock, sleeper=self._sleeper)
        )
        self._require_distributed_gate = require_distributed_gate
        self._gates: dict[str, PacingGate] = {}
        self._lock = threading.Lock()

    def _gate_for(self, provider_key: str) -> PacingGate:
        if not provider_key.strip():
            raise ValueError("provider_key must be non-empty")
        with self._lock:
            gate = self._gates.get(provider_key)
            if gate is None:
                gate = self._gate_factory(provider_key)
                self._gates[provider_key] = gate
            return gate

    def governor(
        self,
        provider_key: str,
        policy: UpstreamPolicy,
        *,
        require_distributed_gate: bool = False,
    ) -> "UpstreamRequestGovernor[object, ResponseLike]":
        gate = self._gate_for(provider_key)
        executor = SafeRequestExecutor(policy, gate=gate, sleeper=self._sleeper)
        return UpstreamRequestGovernor(
            policy,
            executor,
            require_distributed_gate=self._require_distributed_gate or require_distributed_gate,
            clock=self._clock,
            sleeper=self._sleeper,
        )


class UpstreamRequestGovernor(Generic[TaskT, ResultT]):
    def __init__(
        self,
        policy: UpstreamPolicy,
        executor: SafeRequestExecutor,
        *,
        require_distributed_gate: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._policy = policy
        self._executor = executor
        self._clock = clock
        self._sleeper = sleeper
        if require_distributed_gate and executor.gate_scope != "distributed":
            raise UnsafeUpstreamConfiguration(
                "remote multi-instance upstream access requires a distributed pacing gate"
            )

    def plan(self, total_logical_requests: int) -> RequestPlan:
        if total_logical_requests < 0:
            raise ValueError("total_logical_requests must be >= 0")
        max_logical_per_window = max(
            1,
            self._policy.soft_requests_per_window // (self._policy.max_retries + 1),
        )
        max_logical_per_batch = min(
            max_logical_per_window,
            self._policy.max_logical_requests_per_segment,
        )
        batches: list[RequestBatch] = []
        for index, start in enumerate(range(0, total_logical_requests, max_logical_per_batch)):
            stop = min(total_logical_requests, start + max_logical_per_batch)
            size = stop - start
            batches.append(RequestBatch(index, start, stop, size, size * (self._policy.max_retries + 1)))
        return RequestPlan(
            total_logical_requests=total_logical_requests,
            target_utilization=self._policy.target_utilization,
            hard_requests_per_second=self._policy.hard_requests_per_second,
            target_requests_per_second=self._policy.target_requests_per_second,
            window_seconds=self._policy.batch_window_seconds,
            soft_attempts_per_window=self._policy.soft_requests_per_window,
            max_logical_requests_per_batch=max_logical_per_batch,
            batches=tuple(batches),
        )

    def execute(
        self,
        tasks: Sequence[TaskT] | Iterable[TaskT],
        request_for_task: Callable[[TaskT], Callable[[], ResultT]],
    ) -> list[ResultT]:
        materialized = list(tasks)
        plan = self.plan(len(materialized))
        results: list[ResultT] = []
        for batch_position, batch in enumerate(plan.batches):
            self._executor.reset_operation_budget()
            window_started = self._clock()
            for task in materialized[batch.start : batch.stop]:
                results.append(self._executor.run(request_for_task(task)))
            if batch_position < len(plan.batches) - 1:
                elapsed = max(0.0, self._clock() - window_started)
                remaining = self._policy.batch_window_seconds - elapsed
                if remaining > 0:
                    self._sleeper(remaining)
        return results
