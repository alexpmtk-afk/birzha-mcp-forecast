from __future__ import annotations

import json
from pathlib import Path

from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.snapshot import MarketSnapshotService


def main() -> None:
    snapshot = MarketSnapshotService.default().build("SBER")
    payload = snapshot.to_dict()
    evidence = {
        "symbol": payload.get("symbol"),
        "secid": payload.get("secid"),
        "as_of": payload.get("as_of"),
        "source": payload.get("source"),
        "data_quality": payload.get("data_quality"),
        "quality_contract": payload.get("quality_contract"),
        "warnings": payload.get("warnings") or [],
        "d1": payload.get("d1"),
        "h1": payload.get("h1"),
        "m15": payload.get("m15"),
        "flow": payload.get("flow"),
    }
    print("FIRST_REAL_SNAPSHOT=" + json.dumps(evidence, ensure_ascii=False, sort_keys=True))

    if payload.get("data_quality") != "PASS" or (payload.get("quality_contract") or {}).get("status") != "PASS":
        raise SystemExit("FIRST_REAL_FORECAST_CREATE=SKIPPED_DEGRADED_DATA")

    record = build_forecast_from_snapshot(snapshot)
    assert record.validation_status == "UNVALIDATED_BASELINE"
    result = {
        "snapshot": evidence,
        "forecast": record.to_dict(),
        "persistence": "NOT_ATTEMPTED_READONLY_GATE",
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/first_real_sber_forecast.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("FIRST_REAL_FORECAST=" + json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
    print("FIRST_REAL_FORECAST_READONLY_GATE=PASS")


if __name__ == "__main__":
    main()
