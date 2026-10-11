"""Explicit per-capture logical scope for bounded real MOEX HTTP activity.

The scope lives in a ContextVar, never a global shared counter. It covers
instrument resolution, all pages and retry attempts in a single operation.
Production/other provider operations remain unchanged unless explicitly scoped.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import math
import time
from typing import Callable, Iterator


class CaptureScopeRefused(RuntimeError):
    """Known safe diagnostic; never embeds URLs, source bytes or secrets."""

    def __init__(self, safe_reason: str):
        if safe_reason not in {
            "CAPTURE_DEADLINE_EXCEEDED", "CAPTURE_TOTAL_ATTEMPTS_EXCEEDED",
        }:
            raise ValueError("unsupported capture refusal")
        self.safe_reason = safe_reason
        super().__init__(safe_reason)


@dataclass(slots=True)
class CaptureScope:
    deadline_seconds: float = 240.0
    max_attempts: int = 120
    monotonic: Callable[[], float] = time.monotonic
    started: float = field(init=False)
    physical_attempts: int = field(init=False, default=0)

    def __post_init__(self):
        if (not math.isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0 or self.deadline_seconds > 240):
            raise ValueError("invalid bounded capture duration")
        if not 0 < self.max_attempts <= 120:
            raise ValueError("invalid bounded capture request count")
        self.started = self.monotonic()

    def remaining(self) -> float:
        return self.deadline_seconds - (self.monotonic() - self.started)

    def check(self):
        if self.remaining() <= 0:
            raise CaptureScopeRefused("CAPTURE_DEADLINE_EXCEEDED")

    def before_wait(self, delay: float):
        self.check()
        if not math.isfinite(delay) or delay < 0 or delay >= self.remaining():
            raise CaptureScopeRefused("CAPTURE_DEADLINE_EXCEEDED")

    def claim_attempt(self):
        self.check()
        if self.physical_attempts >= self.max_attempts:
            raise CaptureScopeRefused("CAPTURE_TOTAL_ATTEMPTS_EXCEEDED")
        self.physical_attempts += 1


_ACTIVE: ContextVar[CaptureScope | None] = ContextVar(
    "g2_active_market_capture_scope", default=None
)


def active_capture_scope() -> CaptureScope | None:
    return _ACTIVE.get()


@contextmanager
def bounded_market_capture(
    *, max_attempts: int = 120, deadline_seconds: float = 240.0,
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[CaptureScope]:
    if _ACTIVE.get() is not None:
        raise RuntimeError("nested capture scope forbidden")
    scope = CaptureScope(
        deadline_seconds=deadline_seconds,
        max_attempts=max_attempts,
        monotonic=monotonic,
    )
    token = _ACTIVE.set(scope)
    try:
        yield scope
    finally:
        _ACTIVE.reset(token)
