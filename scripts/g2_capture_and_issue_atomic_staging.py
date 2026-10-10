"""Fresh governed MOEX capture followed immediately by atomic STAGING issuance.

Single market per invocation avoids six-market collection delay making early
receipts stale. Never an MCP production journal writer or automated trader.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from scripts.g2_issue_sourcebound_atomic_staging import (
    issue_atomic_staging, _safe_isolated_path, _separate_scopes,
)
from scripts.g2_six_market_live_source_capture import MARKETS, capture_all


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
        raise ValueError("governed source capture failed; refusing forecast issuance")
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
