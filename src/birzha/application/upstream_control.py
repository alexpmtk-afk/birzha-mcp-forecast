"""Upper-level outbound request governor.

Every application service that can cause external API traffic must submit its
logical work through this governor.  The governor owns workload splitting,
safety-headroom pacing and bounded continuation.  Provider adapters only know
how to execute one logical request; they do not get to fan out on their own.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Iterator, Sequence, TypeVar

from birzha.upstream.safety import (
    ResponseLike,
    SafeRequestExecutor,
    UnsafeUpstreamConfiguration,
    UpstreamPolicy,
)

TaskT = TypeVar("TaskT")
ResultT = TypeVar("ResultT", bound=ResponseLike)


@dataclass(frozen=True, slots=True)
class RequestBatch:
    """One safe workload slice scheduled inside a provider time window."""

    index: int
    start: int
    stop: int
    size: int
    worst_case_attempts: int


@dataclass(frozen=True, slots=True)
class RequestPlan:
    """Deterministic explanation of how a large command will be split."""

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


class UpstreamRequestGovernor(Generic[TaskT, ResultT]):
    """Plans and executes arbitrarily large commands without burst fan-out.

    Safety rules:
    * reserve provider headroom (default policy: only 90% of our own ceiling);
    * split work into windows sized for the *worst case* where every logical
      request consumes all configured retries;
    * preserve one shared pacing gate across all batches;
    * reset only the bounded segment counter, never the pacing state;
    * wait out the remainder of each scheduling window before continuing;
    * optionally require a distributed pacing gate for remote multi-instance
      deployment and fail closed when only process-local coordination exists.

    There is intentionally no "force" or "ignore limit" switch.
    """

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

        # The window budget is an attempt budget.  Convert it into a logical
        # request budget using the worst possible retry multiplier.  This is
        # intentionally more conservative than average-case batching.
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
            batches.append(
                RequestBatch(
                    index=index,
                    start=start,
                    stop=stop,
                    size=size,
                    worst_case_attempts=size * (self._policy.max_retries + 1),
                )
            )

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
        """Execute all tasks safely, automatically stretching time as needed."""

        materialized = list(tasks)
        plan = self.plan(len(materialized))
        results: list[ResultT] = []

        for batch_position, batch in enumerate(plan.batches):
            self._executor.reset_operation_budget()
            window_started = self._clock()

            for task in materialized[batch.start : batch.stop]:
                results.append(self._executor.run(request_for_task(task)))

            # A large user request continues automatically, but never by
            # immediately opening another burst window.  Waiting is based on
            # actual elapsed wall time, so retry/backoff time naturally counts.
            if batch_position < len(plan.batches) - 1:
                elapsed = max(0.0, self._clock() - window_started)
                remaining = self._policy.batch_window_seconds - elapsed
                if remaining > 0:
                    self._sleeper(remaining)

        return results
