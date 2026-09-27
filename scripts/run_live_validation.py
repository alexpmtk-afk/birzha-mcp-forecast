from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.market_data import MarketDataService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.application.validation import WalkForwardValidator
from birzha.providers.moex_analytics import MoexAnalyticsClient
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore


CASES = (
    {"symbol": "SBER", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "Si", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "GOLD", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
)

GOLD_PROBE_DATE = date(2026, 5, 20)


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



def _br_public_capture_evidence() -> dict[str, object]:
    control = ProcessUpstreamControlPlane()
    market = MarketDataService.default(control_plane=control)
    analytics = MoexAnalyticsClient(control_plane=control, bearer_token="")
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=market,
        analytics=analytics,
        store=store,
    )
    result = service.capture_public_recent_tradestats("BR")
    secid = str(result["secid"])
    trade_dates = list(result["trade_dates"])
    rows: list[dict[str, object]] = []
    if trade_dates:
        rows = store.read_rows("TRADESTATS", secid, trade_dates[0], trade_dates[-1])
    sample = rows[:2]
    evidence = {**result, "stored_rows": len(rows), "sample": sample}
    print("BR_PUBLIC_CAPTURE=" + json.dumps(evidence, ensure_ascii=False, sort_keys=True))

    if int(result["raw_trades"]) <= 0:
        raise RuntimeError("BR_PUBLIC_CAPTURE_FAIL: no public raw trades received")
    if int(result["derived_5m_rows"]) <= 0 or len(rows) <= 0:
        raise RuntimeError("BR_PUBLIC_CAPTURE_FAIL: no derived rows persisted")
    if any(row.get("val_b") is not None or row.get("val_s") is not None for row in rows):
        raise RuntimeError("BR_PUBLIC_CAPTURE_FAIL: RUB value fields must remain unavailable")
    return evidence


def main() -> int:
    validator = WalkForwardValidator.default()
    gold_resolution = _gold_resolution_evidence(validator)
    br_public_capture = _br_public_capture_evidence()

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
        "br_public_capture": br_public_capture,
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
