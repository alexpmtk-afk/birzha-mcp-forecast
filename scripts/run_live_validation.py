from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from birzha.application.validation import WalkForwardValidator


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


def _quality_diagnostic(
    validator: WalkForwardValidator,
    *,
    symbol: str,
    as_of_date: str,
) -> dict[str, object]:
    """Capture why a mandatory price-quality gate failed without changing it."""
    try:
        snapshot = validator.forecasts.snapshots.build(symbol, as_of_date=as_of_date)
        contract = snapshot.quality_contract
        if contract is not None:
            timeframes = [
                {
                    "timeframe": item.timeframe,
                    "candles": item.candles,
                    "minimum_required": item.minimum_required,
                    "status": item.status,
                    "latest_completed_end": item.latest_completed_end,
                }
                for item in contract.timeframes
            ]
            contract_status = contract.status
            reasons = list(contract.reasons)
        else:
            timeframes = [
                {"timeframe": "D1", "candles": snapshot.d1.candles, "minimum_required": 50},
                {"timeframe": "H1", "candles": snapshot.h1.candles, "minimum_required": 50},
                {"timeframe": "M15", "candles": snapshot.m15.candles, "minimum_required": 50},
            ]
            contract_status = snapshot.data_quality
            reasons = list(snapshot.warnings)
        payload: dict[str, object] = {
            "symbol": symbol,
            "as_of_date": as_of_date,
            "secid": snapshot.secid,
            "snapshot_as_of": snapshot.as_of,
            "status": contract_status,
            "timeframes": timeframes,
            "reasons": reasons,
        }
    except Exception as exc:
        payload = {
            "symbol": symbol,
            "as_of_date": as_of_date,
            "status": "DIAGNOSTIC_ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }
    print("MANDATORY_PRICE_QUALITY_DIAGNOSTIC=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def main() -> int:
    validator = WalkForwardValidator.default()
    gold_resolution = _gold_resolution_evidence(validator)

    reports: list[dict[str, object]] = []
    quality_diagnostics: list[dict[str, object]] = []
    for case in CASES:
        report = validator.run(**case)
        payload = report.to_dict()
        reports.append(payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))

        for failure in payload.get("failures", []):
            failure_text = str(failure)
            if "mandatory_price_quality_incomplete" not in failure_text:
                continue
            failed_date = failure_text.split(":", 1)[0]
            quality_diagnostics.append(
                _quality_diagnostic(
                    validator,
                    symbol=str(case["symbol"]),
                    as_of_date=failed_date,
                )
            )

    evidence = {
        "schema": "BIRZHA_MCP_REAL_MOEX_VALIDATION_V1",
        "purpose": "minimal real-network causal walk-forward end-to-end evidence",
        "quality_acceptance": False,
        "gold_resolution": gold_resolution,
        "reports": reports,
        "quality_diagnostics": quality_diagnostics,
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
