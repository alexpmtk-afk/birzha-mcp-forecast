"""MOEX ISS / ALGOPACK outbound request safety policy.

MOEX's public materials and the current official ``moexalgo`` Python client do
not expose one stable contractual numeric quota that we can safely treat as an
API SLA. The official client itself deliberately spaces requests. Therefore
BIRZHA uses stricter internal limits and treats them as safety ceilings, not as
claims about MOEX's maximum capacity.

These defaults are intentionally conservative for the first remote deployment:
- public ISS: at most 2 requests/second per server process;
- authenticated / ALGOPACK path: at most 1 request/second per server process;
- bounded request count per logical operation;
- 429 and transient 5xx responses trigger bounded exponential backoff;
- retries consume the same request budget.

Provider adapters MUST use these policies or a stricter replacement.
"""

from __future__ import annotations

from .safety import UpstreamPolicy

MOEX_ISS_PUBLIC_POLICY = UpstreamPolicy(
    min_interval_seconds=0.5,
    max_requests_per_operation=120,
    max_retries=2,
    base_backoff_seconds=1.0,
    max_backoff_seconds=30.0,
)

MOEX_AUTHENTICATED_POLICY = UpstreamPolicy(
    min_interval_seconds=1.0,
    max_requests_per_operation=120,
    max_retries=2,
    base_backoff_seconds=2.0,
    max_backoff_seconds=60.0,
)


def policy_for_moex(*, authenticated: bool) -> UpstreamPolicy:
    return MOEX_AUTHENTICATED_POLICY if authenticated else MOEX_ISS_PUBLIC_POLICY
