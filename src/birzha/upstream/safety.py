"""Conservative outbound request safety controls for upstream market-data APIs.

This module is deliberately provider-neutral. Provider adapters are not
allowed to decide their own pacing on the fly; they receive a policy and a
shared pacing gate from the application-level request governor.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol, TypeVar

T = TypeVar("T", bound="ResponseLike")


class ResponseLike(Protocol):
    status_code: int
    headers: object


class PacingGate(Protocol):
    """Coordinates the next allowed outbound attempt for one provider scope."""

    scope: str

    def pace(self, min_interval_seconds: float) -> None: ...


class UpstreamRequestBudgetExceeded(RuntimeError):
    """Raised when a logical segment exhausts its bounded attempt budget."""


class UpstreamCaptureDeadlineExceeded(RuntimeError):
    """A governed capture cannot finish inside its shared monotonic deadline."""

    safe_reason = "UPSTREAM_CAPTURE_DEADLINE_EXCEEDED"


class UpstreamCaptureAttemptBudgetExceeded(RuntimeError):
    """A governed capture exhausted its cumulative physical-attempt budget."""

    safe_reason = "UPSTREAM_CAPTURE_ATTEMPT_BUDGET_EXHAUSTED"


@dataclass(slots=True)
class CaptureAttemptBudget:
    """One capture's cumulative physical HTTP attempts and monotonic deadline."""

    started_at: float
    deadline_at: float
    max_attempts: int
    attempts_used: int = 0

    def remaining_seconds(self, clock: Callable[[], float]) -> float:
        return max(0.0, self.deadline_at - clock())

    def elapsed_seconds(self, clock: Callable[[], float]) -> float:
        return max(0.0, clock() - self.started_at)

    def ensure_time(self, clock: Callable[[], float]) -> None:
        if clock() >= self.deadline_at:
            raise UpstreamCaptureDeadlineExceeded(
                "governed capture exceeded its shared deadline"
            )

    def ensure_wait_fits(self, seconds: float, clock: Callable[[], float]) -> None:
        self.ensure_time(clock)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("capture wait must be finite and non-negative")
        if seconds >= self.remaining_seconds(clock):
            raise UpstreamCaptureDeadlineExceeded(
                "required upstream wait does not fit the remaining capture deadline"
            )

    def consume_attempt(self, clock: Callable[[], float]) -> None:
        self.ensure_time(clock)
        if self.attempts_used >= self.max_attempts:
            raise UpstreamCaptureAttemptBudgetExceeded(
                f"capture physical-attempt budget exhausted at {self.attempts_used} attempts"
            )
        self.attempts_used += 1

    def summary(self, clock: Callable[[], float]) -> dict[str, int | float]:
        return {
            "physical_attempts_used": self.attempts_used,
            "physical_attempt_limit": self.max_attempts,
            "elapsed_seconds": round(self.elapsed_seconds(clock), 6),
            "deadline_seconds": round(self.deadline_at - self.started_at, 6),
        }


class UpstreamRateLimited(RuntimeError):
    """Final attempt diagnosis; safe fields never contain provider text."""

    def __init__(self, message: str, *, safe_reason: str = "UPSTREAM_RETRIES_EXHAUSTED"):
        super().__init__(message)
        self.safe_reason = safe_reason


class UnsafeUpstreamConfiguration(RuntimeError):
    """Raised when remote market data would run without the required safety gate."""


@dataclass(frozen=True, slots=True)
class UpstreamPolicy:
    """Fail-safe limits for one upstream provider profile.

    ``min_interval_seconds`` is the hard internal ceiling interval. BIRZHA
    intentionally operates below it: ``target_utilization`` defaults to 0.90,
    so the real pacing interval is larger by ``1 / target_utilization``.

    ``batch_window_seconds`` is used by the upper-level governor to split a
    large user command into smaller windows. Retries are additionally paced
    by the same shared gate and count toward the bounded segment attempt budget.
    """

    min_interval_seconds: float
    max_requests_per_operation: int
    max_retries: int = 2
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    target_utilization: float = 0.90
    batch_window_seconds: float = 10.0
    no_header_429_cooldown_seconds: float = 60.0

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
        if not 0 < self.target_utilization < 1:
            raise ValueError("target_utilization must be > 0 and < 1")
        if self.batch_window_seconds <= 0:
            raise ValueError("batch_window_seconds must be > 0")
        if self.no_header_429_cooldown_seconds <= 0:
            raise ValueError("no_header_429_cooldown_seconds must be > 0")
        if self.max_requests_per_operation < self.max_retries + 1:
            raise ValueError("operation budget must accommodate at least one fully retried request")

    @property
    def hard_requests_per_second(self) -> float:
        return 1.0 / self.min_interval_seconds

    @property
    def target_requests_per_second(self) -> float:
        return self.hard_requests_per_second * self.target_utilization

    @property
    def effective_min_interval_seconds(self) -> float:
        """Actual minimum gap used by BIRZHA after reserving safety headroom."""

        return self.min_interval_seconds / self.target_utilization

    @property
    def soft_requests_per_window(self) -> int:
        """Maximum attempts planned inside one upper-level scheduling window."""

        return max(
            1,
            math.floor(self.batch_window_seconds * self.target_requests_per_second + 1e-12),
        )

    @property
    def max_logical_requests_per_segment(self) -> int:
        """Logical requests that fit even if every request uses every retry."""

        return max(1, self.max_requests_per_operation // (self.max_retries + 1))


class ProcessPacingGate:
    """Thread-safe, process-wide serialization gate.

    A single instance must be shared by all commands for the same provider
    profile. It prevents two concurrent MCP calls inside one container process
    from multiplying the outbound request rate.

    This gate is intentionally marked ``scope='process'``. Remote multi-instance
    market-data enablement must use a distributed implementation of ``PacingGate``
    or an equivalently strict deployment constraint; the application governor
    can fail closed when distributed scope is required.
    """

    scope = "process"

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._next_request_at = 0.0
        self._lock = threading.Lock()

    def pace(self, min_interval_seconds: float) -> None:
        self.pace_until(min_interval_seconds, deadline_at=None)

    def pace_until(self, min_interval_seconds: float, *, deadline_at: float | None) -> None:
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be > 0")
        with self._lock:
            now = self._clock()
            delay = self._next_request_at - now
            if delay > 0:
                if deadline_at is not None and now + delay >= deadline_at:
                    raise UpstreamCaptureDeadlineExceeded(
                        "provider pacing wait does not fit the remaining capture deadline"
                    )
                self._sleeper(delay)
                now = self._clock()
                if deadline_at is not None and now >= deadline_at:
                    raise UpstreamCaptureDeadlineExceeded(
                        "provider pacing wait exceeded the capture deadline"
                    )
            self._next_request_at = max(now, self._next_request_at) + min_interval_seconds


class SafeRequestExecutor:
    """Budgeted, retry-aware executor for one provider profile.

    The executor handles attempt-level safety. Large-command splitting lives
    one layer above in ``birzha.application.upstream_control``.
    """

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        policy: UpstreamPolicy,
        *,
        gate: PacingGate | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._gate = gate or ProcessPacingGate(sleeper=sleeper)
        self._sleeper = sleeper
        self._jitter = jitter
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._requests_used = 0
        self._total_requests_used = 0
        self._capture_budget: ContextVar[CaptureAttemptBudget | None] = ContextVar(
            f"upstream_capture_budget_{id(self)}", default=None
        )

    @property
    def capture_remaining_seconds(self) -> float | None:
        budget = self._capture_budget.get()
        return None if budget is None else budget.remaining_seconds(self._monotonic_clock)

    @contextmanager
    def capture_scope(self, *, max_attempts: int, timeout_seconds: float) -> Iterator[CaptureAttemptBudget]:
        if max_attempts <= 0:
            raise ValueError("capture max_attempts must be > 0")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("capture timeout_seconds must be finite and > 0")
        if self._capture_budget.get() is not None:
            raise ValueError("nested upstream capture scopes are not supported")
        started = self._monotonic_clock()
        budget = CaptureAttemptBudget(started, started + timeout_seconds, max_attempts)
        token: Token[CaptureAttemptBudget | None] = self._capture_budget.set(budget)
        try:
            yield budget
        finally:
            self._capture_budget.reset(token)

    def capture_summary(self, budget: CaptureAttemptBudget) -> dict[str, int | float]:
        return budget.summary(self._monotonic_clock)

    def sleep_with_capture_deadline(self, seconds: float) -> None:
        budget = self._capture_budget.get()
        if budget is not None:
            budget.ensure_wait_fits(seconds, self._monotonic_clock)
        self._sleeper(seconds)
        if budget is not None:
            budget.ensure_time(self._monotonic_clock)

    @property
    def requests_used(self) -> int:
        """Attempts used in the current bounded logical segment."""

        return self._requests_used

    @property
    def total_requests_used(self) -> int:
        """Attempts used by this executor across all reset segments."""

        return self._total_requests_used

    @property
    def gate_scope(self) -> str:
        return self._gate.scope

    def reset_operation_budget(self) -> None:
        """Start a new bounded segment while preserving shared pacing state."""

        self._requests_used = 0

    def _consume_budget(self) -> None:
        if self._requests_used >= self._policy.max_requests_per_operation:
            raise UpstreamRequestBudgetExceeded(
                f"request budget exhausted at {self._requests_used} attempts"
            )
        self._requests_used += 1
        self._total_requests_used += 1

    def _retry_after_seconds(self, response: ResponseLike) -> float | None:
        # HTTP field names are case insensitive; httpx dict keys are lowercase.
        try:
            raw = next((v for k, v in response.headers.items()
                        if str(k).lower() == "retry-after"), None)
        except AttributeError:
            return None
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            try:
                deadline = parsedate_to_datetime(raw)
                if deadline.tzinfo is None:
                    return None
                value = max(0.0, (deadline - self._wall_clock()).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None
        return value if math.isfinite(value) and value >= 0 else None

    def _backoff_seconds(self, attempt: int, response: ResponseLike) -> float:
        retry_after = self._retry_after_seconds(response)
        if retry_after is not None:
            # Never shorten an upstream-specified Retry-After. Retrying earlier
            # than the server asked is unsafe even when it exceeds our normal
            # exponential-backoff ceiling.
            return retry_after
        if int(response.status_code) == 429:
            # A 429 without guidance gets a deliberately conservative cooldown,
            # not the ordinary 1s/2s transient-error backoff.
            return self._policy.no_header_429_cooldown_seconds
        exponential = self._policy.base_backoff_seconds * (2**attempt)
        with_jitter = exponential + min(1.0, exponential * 0.1) * self._jitter()
        return min(with_jitter, self._policy.max_backoff_seconds)

    def run(self, request: Callable[[], T]) -> T:
        """Execute one HTTP operation with reserved headroom and bounded retries."""

        last_status: int | None = None
        capture_budget = self._capture_budget.get()
        for attempt in range(self._policy.max_retries + 1):
            self._consume_budget()
            if capture_budget is not None:
                capture_budget.ensure_time(self._monotonic_clock)
            pace_until = getattr(self._gate, "pace_until", None)
            if capture_budget is not None and not callable(pace_until):
                raise UnsafeUpstreamConfiguration(
                    "pacing gate must support capture deadlines"
                )
            if capture_budget is not None:
                pace_until(
                    self._policy.effective_min_interval_seconds,
                    deadline_at=capture_budget.deadline_at,
                )
            else:
                self._gate.pace(self._policy.effective_min_interval_seconds)
            if capture_budget is not None:
                capture_budget.consume_attempt(self._monotonic_clock)
            response = request()
            if capture_budget is not None:
                capture_budget.ensure_time(self._monotonic_clock)
            last_status = int(response.status_code)
            if last_status not in self.RETRYABLE_STATUS_CODES:
                return response
            if attempt >= self._policy.max_retries:
                break
            self.sleep_with_capture_deadline(self._backoff_seconds(attempt, response))

        # Transport failures are synthetic 503 responses, not provider HTTP 503.
        transport = getattr(response, "transport_failure", False)
        reason = ("UPSTREAM_TIMEOUT_OR_TRANSPORT" if transport else
                  "HTTP_429_RATE_LIMIT" if last_status == 429 else
                  "HTTP_503_UNAVAILABLE" if last_status == 503 else
                  "HTTP_5XX_UPSTREAM")
        raise UpstreamRateLimited(
            f"upstream remained rate-limited/unavailable after retries; last_status={last_status}",
            safe_reason=reason,
        )
