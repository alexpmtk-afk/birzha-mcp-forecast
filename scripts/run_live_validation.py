from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path

from birzha.application.market_data import MOEX_TIMEZONE
from birzha.application.validation import WalkForwardValidator
from birzha.providers.moex_analytics import MoexAnalyticsClient


CASES = (
    {"symbol": "SBER", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "Si", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "GOLD", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
)

GOLD_PROBE_DATE = date(2026, 5, 20)



INSTRUMENT_CONTRACT_CASES = {
    "SBER": "equity",
    "Si": "future",
    "BR": "future",
    "GOLD": "future",
    "IMOEX": "index",
    "RTSI": "index",
}


def _instrument_contract_evidence(
    validator: WalkForwardValidator,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for symbol, expected_asset_class in INSTRUMENT_CONTRACT_CASES.items():
        instrument = validator.market_data.resolve(symbol)
        payload = instrument.to_dict()
        failures: list[str] = []

        if payload.get("asset_class") != expected_asset_class:
            failures.append("asset_class")
        if not payload.get("calendar_id"):
            failures.append("calendar_id")
        if not payload.get("session_profile"):
            failures.append("session_profile")
        if not payload.get("data_capabilities"):
            failures.append("data_capabilities")

        if expected_asset_class == "future":
            if payload.get("roll_policy") != "MOEX_CAUSAL_LIQUIDITY_AS_OF_DATE":
                failures.append("roll_policy")
            if not payload.get("root_symbol"):
                failures.append("root_symbol")
            if not payload.get("expiration_date"):
                failures.append("expiration_date")
            for field in ("tick_size", "tick_value", "contract_multiplier"):
                if payload.get(field) is None:
                    failures.append(field)
        else:
            if payload.get("roll_policy") != "NOT_APPLICABLE":
                failures.append("roll_policy")

        if symbol == "SBER":
            for field in ("currency", "tick_size", "contract_multiplier"):
                if payload.get(field) is None:
                    failures.append(field)

        row = {
            "symbol": symbol,
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "instrument": payload,
        }
        evidence.append(row)
        print(
            "INSTRUMENT_CONTRACT="
            + json.dumps(row, ensure_ascii=False, sort_keys=True)
        )

    failed = [item for item in evidence if item["status"] != "PASS"]
    if failed:
        raise RuntimeError(
            "INSTRUMENT_CONTRACT_GATE_FAIL: "
            + json.dumps(failed, ensure_ascii=False, sort_keys=True)
        )
    print("INSTRUMENT_CONTRACT_GATE=PASS")
    return evidence



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



def _gold_public_tail_evidence(
    validator: WalkForwardValidator,
) -> dict[str, object]:
    today = datetime.now(MOEX_TIMEZONE).date()
    instrument = validator.market_data.resolve("GOLD", as_of=today)
    client = MoexAnalyticsClient(bearer_token="")
    rows, checkpoint, complete = client.fetch_public_recent_trade_page(
        instrument,
        start=0,
        page_limit=10,
    )
    if not rows:
        raise RuntimeError(
            "GOLD_PUBLIC_TAIL_FAIL: public ISS returned no delayed trades"
        )
    recnos = [
        int(row.get("RECNO") or row.get("recno") or 0)
        for row in rows
    ]
    if checkpoint <= 0 or max(recnos, default=0) != checkpoint:
        raise RuntimeError(
            "GOLD_PUBLIC_TAIL_FAIL: invalid RECNO checkpoint"
        )
    evidence = {
        "date": today.isoformat(),
        "secid": instrument.secid,
        "rows": len(rows),
        "checkpoint_recno": checkpoint,
        "complete": complete,
    }
    print(
        "GOLD_PUBLIC_TAIL=PASS "
        f"secid={instrument.secid} rows={len(rows)} recno={checkpoint}"
    )
    return evidence

def main() -> int:
    validator = WalkForwardValidator.default()
    instrument_contract = _instrument_contract_evidence(validator)
    gold_resolution = _gold_resolution_evidence(validator)
    gold_public_tail = _gold_public_tail_evidence(validator)

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
        "instrument_contract": instrument_contract,
        "gold_resolution": gold_resolution,
        "gold_public_tail": gold_public_tail,
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
