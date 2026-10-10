"""Fresh governed MOEX capture followed immediately by atomic STAGING issuance.

Single market per invocation avoids six-market collection delay making early
receipts stale. Never an MCP production journal writer or automated trader.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts.g2_issue_sourcebound_atomic_staging import (
    issue_atomic_staging, _safe_isolated_path, _separate_scopes,
)
from scripts.g2_six_market_live_source_capture import MARKETS, capture_all


SAFE_FAILURE_REASONS = {
    "maximum governed source pages exceeded": "MAX_SOURCE_PAGES_EXCEEDED",
    "source pagination changed during capture": "PAGINATION_DRIFT",
    "unexpected empty paginated page": "PREMATURE_EMPTY_PAGE",
    "duplicate or unsorted response sessions": "DUPLICATE_OR_UNORDERED_BARS",
    "no completed source candles": "NO_COMPLETED_CANDLES",
    "no successful governed source body": "HTTP_RESPONSE_UNAVAILABLE",
    "noncausal or excessively long request clock interval": "REQUEST_TIME_INVALID",
    "MOEX row outside pinned exchange-local window": "OUT_OF_PINNED_WINDOW",
    "forming or unobserved candle included": "UNFINISHED_SOURCE_CANDLE",
}


def _safe_failure_diagnostic(failures):
    """Never echo arbitrary provider exceptions (may contain URLs/tokens)."""
    if not isinstance(failures, list):
        return "UNCLASSIFIED_SOURCE_FAILURE"
    codes = []
    for row in failures:
        if not isinstance(row, dict):
            codes.append("UNCLASSIFIED_SOURCE_FAILURE")
            continue
        detail = str(row.get("detail", ""))
        error_type = str(row.get("error_type", ""))
        # The upstream client turns HTTP status into MoexIssError and may
        # include an endpoint path in its message. Extract ONLY the status.
        # Never echo arbitrary message contents or provider URL/query.
        http = (re.search(r"^MOEX ISS returned HTTP ([1-5][0-9]{2})\b", detail)
                if error_type == "MoexIssError" else None)
        if http:
            status = int(http.group(1))
            match = (
                "HTTP_429_RATE_LIMIT" if status == 429 else
                "HTTP_403_ACCESS_DENIED" if status == 403 else
                "HTTP_503_UNAVAILABLE" if status == 503 else
                "HTTP_5XX_UPSTREAM" if status >= 500 else
                "HTTP_OTHER_NON_200"
            )
        elif error_type in {"TimeoutError", "ConnectTimeout", "ReadTimeout",
                            "ConnectError", "TransportError"}:
            match = "UPSTREAM_TIMEOUT_OR_TRANSPORT"
        elif error_type in {"UnsafeUpstreamConfiguration",
                            "UpstreamSafetyError", "UpstreamBudgetExceeded"}:
            match = "UPSTREAM_GOVERNOR_BLOCKED"
        else:
            match = next((code for phrase, code in SAFE_FAILURE_REASONS.items()
                          if phrase in detail), "UNCLASSIFIED_SOURCE_FAILURE")
        codes.append(match)
    return ",".join(codes) or "UNCLASSIFIED_SOURCE_FAILURE"


def capture_and_issue_one(
    provider, market_data, market, capture_dir: Path, staging_root: Path, *,
    clock=lambda: datetime.now(timezone.utc), fault_hook=None,
):
    if market not in MARKETS:
        raise ValueError("unsupported market root")
    # Reject known HOME/production and symlinked parents *before* network
    # capture creates folders or writes original HTTP response bytes.
    capture_dir = _safe_isolated_path(capture_dir, existing_directory=False)
    staging_root = _safe_isolated_path(staging_root, existing_directory=True)
    _separate_scopes(capture_dir, staging_root)
    manifest = capture_all(
        provider, market_data, capture_dir, clock=clock, markets=(market,)
    )
    if (manifest["verified_complete_market_count"] != 1
        or manifest["failed_markets"]
        or manifest["complete_markets"] != [market]):
        raise ValueError(
            "governed source capture failed; refusing forecast issuance; "
            "safe_reason=" + _safe_failure_diagnostic(manifest.get("failed_markets"))
        )
    result = issue_atomic_staging(
        capture_dir, staging_root, market, clock=clock, fault_hook=fault_hook
    )
    return {
        **result,
        "capture_mode": "BOUNDED_LIVE_MOEX_ISS_NETWORK_SINGLE_MARKET",
        "independent_provider_and_clock_attestation": False,
        "strict_ex_ante_proof": False,
        "production_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", required=True, choices=MARKETS)
    parser.add_argument("--capture-dir", required=True, type=Path)
    parser.add_argument("--staging-root", required=True, type=Path)
    args = parser.parse_args(argv)
    from birzha.application.market_data import MarketDataService
    from birzha.application.upstream_control import ProcessUpstreamControlPlane
    from birzha.providers.moex_iss import MoexIssClient
    from birzha.providers.moex_resolver import MoexDirectInstrumentResolver
    control = ProcessUpstreamControlPlane()
    provider = MoexIssClient(control_plane=control)
    market_data = MarketDataService(
        provider=provider, direct_resolver=MoexDirectInstrumentResolver(provider)
    )
    result = capture_and_issue_one(
        provider, market_data, args.market, args.capture_dir, args.staging_root
    )
    import json
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
