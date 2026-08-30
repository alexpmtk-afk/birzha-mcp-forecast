"""Conservative outbound request safety controls for upstream market-data APIs."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

T = TypeVar("T", bound="ResponseLike")


class ResponseLike(Protocol):
    status_code: int
    headers: object


class UpstreamRequestBudgetExceeded(RuntimeError):
    """Raised when a caller tries to exceed a per-operation request budget."""


class UpstreamRateLimited(RuntimeError):
    """Raised when the upstream explicitly rate-limits or remains unavailable."""


@dataclass(frozen=True, slots=True)
class UpstreamPolicy:
    """Fail-safe limits for one upstream provider profile.

    ``min_interval_seconds`` is the minimum wall-clock gap between requests in a
    single process. ``max_requests_per_operation`` caps a logical operation so
    pagination/bugs cannot fan out indefinitely. Retries count toward the same
    budget. ``max_retries`` is intentionally small.
    """

    min_interval_seconds: float
    max_requests_per_operation: int
    max_retries: int = 2
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be > 0")
        if self.max_requests_per_operation <= 0:
            raise ValueError("max_requests_per_operation must be > 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.base_backoff_seconds <= 0 or self.max_backoff_seconds <= 0:
            raise ValueError("backoff values must be > 0")
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("base_backoff_seconds must be <= max_backoff_seconds")


class SafeRequestExecutor:
    """Serial, budgeted, retry-aware executor for outbound HTTP calls."""

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        policy: UpstreamPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter
        self._next_request_at = 0.0
        self._requests_used = 0

    @property
    def requests_used(self) -> int:
        return self._requests_used

    def _consume_budget(self) -> None:
        if self._requests_used >= self._policy.max_requests_per_operation:
            raise UpstreamRequestBudgetExceeded(
                f"request budget exhausted at {self._requests_used} requests"
            )
        self._requests_used += 1

    def _pace(self) -> None:
        now = self._clock()
        delay = self._next_request_at - now
        if delay > 0:
            self._sleeper(delay)
            now = self._clock()
        self._next_request_at = max(now, self._next_request_at) + self._policy.min_interval_seconds

    @staticmethod
    def _retry_after_seconds(response: ResponseLike) -> float | None:
        try:
            raw = response.headers.get("Retry-After")  # type: ignore[attr-defined]
        except AttributeError:
            return None
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def _backoff_seconds(self, attempt: int, response: ResponseLike) -> float:
        retry_after = self._retry_after_seconds(response)
        if retry_after is not None:
            return min(retry_after, self._policy.max_backoff_seconds)
        exponential = self._policy.base_backoff_seconds * (2**attempt)
        with_jitter = exponential + min(1.0, exponential * 0.1) * self._jitter()
        return min(with_jitter, self._policy.max_backoff_seconds)

    def run(self, request: Callable[[], T]) -> T:
        """Execute one HTTP operation with pacing, bounded retries and budget."""

        last_status: int | None = None
        for attempt in range(self._policy.max_retries + 1):
            self._consume_budget()
            self._pace()
            response = request()
            last_status = int(response.status_code)
            if last_status not in self.RETRYABLE_STATUS_CODES:
                return response
            if attempt >= self._policy.max_retries:
                break
            self._sleeper(self._backoff_seconds(attempt, response))

        raise UpstreamRateLimited(
            f"upstream remained rate-limited/unavailable after retries; last_status={last_status}"
        )
