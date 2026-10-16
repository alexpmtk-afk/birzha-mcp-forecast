"""Generate a frozen NO-NETWORK schedule/rule manifest for the G2 pilot.

This is a design preflight only: no live data, source connections, forecasts,
trading, protected data, or HOME/server changes. Exclusive local write means
local no-replacement, NOT independent timestamp authority or PIT certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

VERSION = "G2_STRUCTURE_PILOT_PREREG_V1_20261017"
DAYS = (
    "2026-10-20", "2026-10-22", "2026-10-26", "2026-10-28",
    "2026-10-30", "2026-11-03", "2026-11-05", "2026-11-09",
    "2026-11-11", "2026-11-13", "2026-11-17", "2026-11-19",
)
ROOTS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")


def frozen_plan() -> dict:
    slots = [
        {
            "root": root,
            "t0_moscow": f"{day}T13:10:00+03:00",
            "status": "PLANNED_NOT_CAPTURED",
            "exact_secid": None if root in {"Si", "BR", "GOLD"} else root,
            "calendar_eligibility": "PENDING_AUTHENTICATED_SOURCE",
        }
        for day in DAYS for root in ROOTS
    ]
    return {
        "version": VERSION,
        "research_only": True,
        "not_started": True,
        "timezone": "Europe/Moscow UTC+03:00",
        "t0": "13:10:00+03:00 on listed dates only",
        "no_reschedule": True,
        "n_slots": len(slots),
        "n_dates": len(DAYS),
        "markets": list(ROOTS),
        "candidate": {
            "up": "D1=HH_HL and H1=HH_HL",
            "down": "D1=LH_LL and H1=LH_LL",
            "abstain": "otherwise, missing data or unverified source/contract/time",
            "magnitude_for_h": "P0 + sign * Wilder_D1_ATR14_price * sqrt(h)",
            "feature_version": "MARKET_STRUCTURE_FACTS_V2_20261017",
        },
        "horizons_sessions": [5, 10, 20],
        "comparators": {
            "flat": "P0",
            "momentum": "P0 * (1 + (P0 / D1_close_5_sessions_ago - 1) * H/5)",
        },
        "futures_exact_contract_selection":
            "pre-T0 eligible, expiry strictly after session H20, max prior-completed-session volume, SECID tie-break",
        "first_receipt_required_for_prediction": True,
        "source_raw_sha_and_receipt_required": True,
        "append_only_snapshot_when_authorized": True,
        "independent_timestamp_authority_provided": False,
        "real_capture_enabled": False,
        "released_forecast_model": False,
        "code_or_model_change_during_pilot": False,
        "slots": slots,
    }


def encoded_plan() -> bytes:
    return (json.dumps(frozen_plan(), ensure_ascii=False, sort_keys=True,
                       indent=2) + "\n").encode("utf-8")


def write_exclusive(path: Path) -> str:
    """x-mode no overwrite; no false promise of global or third-party immutability."""
    raw = encoded_plan()
    with path.open("xb") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    return hashlib.sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sha = write_exclusive(args.output)
    print(json.dumps({"status": "PREREG_PLAN_WRITTEN", "path": str(args.output),
                      "sha256": sha, "slots": len(DAYS)*len(ROOTS),
                      "live_capture": False}, sort_keys=True))


if __name__ == "__main__":
    main()
