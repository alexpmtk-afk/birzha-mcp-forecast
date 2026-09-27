from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from birzha.application.validation import WalkForwardValidator
from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import MoexAnalyticsClient


CASES = (
    {"symbol": "SBER", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "Si", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "GOLD", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
)

GOLD_PROBE_DATE = date(2026, 5, 20)
BR_FUTOI_FROM = "2026-09-12"
BR_FUTOI_TILL = "2026-09-27"
BR_FUTURE = Instrument(
    symbol="BR",
    secid="BRV6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="BR",
)


def _gold_resolution_evidence(validator: WalkForwardValidator) -> dict[str, object]:
    instrument = validator.market_data.resolve("GOLD", as_of=GOLD_PROBE_DATE)
    payload = instrument.to_dict()
    print("GOLD_RESOLUTION=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))

    secid = str(payload.get("secid", "")).upper()
    valid = (
        payload.get("asset_class") == "future"
        and payload.get("engine") == "futures"
        and payload.get("market") == "forts"
        and secid != "GOLD"
        and secid.startswith("GD")
    )
    if not valid:
        raise RuntimeError(
            "GOLD_FUTURES_RESOLUTION_FAIL: expected historical GOLD to resolve "
            "to a MOEX GD* futures contract"
        )
    print(f"GOLD_FUTURES_RESOLUTION=PASS secid={secid}")
    return payload



def _br_futoi_evidence() -> dict[str, object]:
    client = MoexAnalyticsClient(bearer_token="")
    rows = client.fetch_futoi(
        BR_FUTURE,
        from_date=BR_FUTOI_FROM,
        till_date=BR_FUTOI_TILL,
    )
    dates = sorted(
        {
            str(row.get("tradedate") or row.get("TRADEDATE") or "")
            for row in rows
            if row.get("tradedate") or row.get("TRADEDATE")
        }
    )
    summary = {
        "from_date": BR_FUTOI_FROM,
        "till_date": BR_FUTOI_TILL,
        "row_count": len(rows),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "dates": dates,
        "rows": rows,
    }
    print(
        "BR_FUTOI_EVIDENCE="
        + json.dumps(
            {key: value for key, value in summary.items() if key != "rows"},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return summary


def main() -> int:
    validator = WalkForwardValidator.default()
    gold_resolution = _gold_resolution_evidence(validator)
    br_futoi = _br_futoi_evidence()

    reports: list[dict[str, object]] = []
    for case in CASES:
        report = validator.run(**case)
        payload = report.to_dict()
        reports.append(payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    evidence = {
        "schema": "BIRZHA_MCP_REAL_MOEX_VALIDATION_V1",
        "purpose": "minimal real-network causal walk-forward end-to-end evidence",
        "quality_acceptance": False,
        "gold_resolution": gold_resolution,
        "br_futoi_backfill": br_futoi,
        "reports": reports,
    }
    output = Path("artifacts/real_moex_validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    # This gate proves only that the real historical path completes for a share
    # and configured futures roots, including explicit GOLD -> GD* resolution.
    # It must never be interpreted as evidence of statistical model quality;
    # that requires the governed six-market study.
    failed = [
        r
        for r in reports
        if r["status"] not in {"COMPUTED", "PARTIAL"}
        or int(r["completed_forecasts"]) < 1
    ]
    if failed:
        print("REAL_MOEX_VALIDATION_GATE=FAIL")
        return 1
    print("REAL_MOEX_VALIDATION_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
