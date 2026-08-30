"""Shared outbound API safety controls.

All external market-data providers must route HTTP traffic through these controls.
Provider adapters may add stricter limits, but must not bypass them.
"""

from .safety import (
    PacingGate,
    ProcessPacingGate,
    ResponseLike,
    SafeRequestExecutor,
    UnsafeUpstreamConfiguration,
    UpstreamPolicy,
    UpstreamRateLimited,
    UpstreamRequestBudgetExceeded,
)

__all__ = [
    "PacingGate",
    "ProcessPacingGate",
    "ResponseLike",
    "SafeRequestExecutor",
    "UnsafeUpstreamConfiguration",
    "UpstreamPolicy",
    "UpstreamRateLimited",
    "UpstreamRequestBudgetExceeded",
]
