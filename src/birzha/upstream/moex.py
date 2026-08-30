"""MOEX ISS / ALGOPACK outbound request safety policy.

MOEX's public materials and the current official ``moexalgo`` Python client do
not expose one stable contractual numeric quota that BIRZHA can safely treat as
an API SLA.  The official client itself deliberately spaces requests.  BIRZHA
therefore defines stricter internal ceilings and then deliberately uses only
90% of those ceilings.

Internal hard ceilings for the first provider implementation:
- public ISS: 2 attempts/second maximum internal ceiling;
- authenticated / ALGOPACK: 1 attempt/second maximum internal ceiling.

Actual BIRZHA target after 10% reserved headroom:
- public ISS: 1.8 attempts/second;
- authenticated / ALGOPACK: 0.9 attempts/second.

Large commands are not rejected merely because they are large.  The application
request governor splits them into bounded windows and stretches execution over
time.  Retries count against the same safety budget.  HTTP 429 and transient
5xx responses trigger bounded Retry-After/exponential backoff.

These values are BIRZHA safety ceilings, not claims about MOEX's contractual
maximum.  If MOEX publishes a stricter endpoint/account-specific rule, the
stricter rule must replace these defaults before that endpoint is enabled.
"""

from __future__ import annotations

from .safety import UpstreamPolicy

MOEX_ISS_PUBLIC_POLICY = UpstreamPolicy(
    min_interval_seconds=0.5,  # hard internal ceiling 2 req/s
    max_requests_per_operation=120,
    max_retries=2,
    base_backoff_seconds=1.0,
    max_backoff_seconds=30.0,
    target_utilization=0.90,
    batch_window_seconds=10.0,
)

MOEX_AUTHENTICATED_POLICY = UpstreamPolicy(
    min_interval_seconds=1.0,  # hard internal ceiling 1 req/s
    max_requests_per_operation=120,
    max_retries=2,
    base_backoff_seconds=2.0,
    max_backoff_seconds=60.0,
    target_utilization=0.90,
    batch_window_seconds=10.0,
)


def policy_for_moex(*, authenticated: bool) -> UpstreamPolicy:
    return MOEX_AUTHENTICATED_POLICY if authenticated else MOEX_ISS_PUBLIC_POLICY
