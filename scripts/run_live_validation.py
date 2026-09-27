from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from urllib.parse import urlencode

from birzha.application.market_data import MarketDataService
from birzha.application.validation import WalkForwardValidator
from birzha.providers.moex_analytics import ISS_BASE, MoexAnalyticsClient


CASES = (
    {"symbol": "SBER", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "Si", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "GOLD", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
)

GOLD_PROBE_DATE = date(2026, 5, 20)
BR_TRADESTATS_PROBE_DATES = ("2026-09-11", "2026-08-28", "2026-05-20")


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


def _br_public_tradestats_evidence() -> list[dict[str, object]]:
    market_data = MarketDataService.default()
    analytics = MoexAnalyticsClient(bearer_token="")
    evidence: list[dict[str, object]] = []

    for day in BR_TRADESTATS_PROBE_DATES:
        instrument = market_data.resolve("BR", as_of=date.fromisoformat(day))
        path = f"/datashop/algopack/fo/tradestats/{instrument.secid}.json"
        params: dict[str, object] = {
            "iss.meta": "off",
            "from": day,
            "till": day,
            "limit": 1000,
            "start": 0,
        }
        url = f"{ISS_BASE}{path}?{urlencode(params)}"
        response = analytics._algopack_governor.execute(  # noqa: SLF001 - temporary live probe
            [(ISS_BASE, path, params)],
            lambda task: lambda: analytics._one_attempt(task[0], task[1], task[2]),  # noqa: SLF001
        )[0]
        body_text = response.body.decode("utf-8", "replace")
        snippet = body_text[:1000]
        item = {
            "date": day,
            "secid": instrument.secid,
            "last_trade_date": instrument.last_trade_date,
            "host": "iss.moex.com",
            "url": url,
            "status": response.status_code,
            "body_snippet": snippet,
        }
        evidence.append(item)
        print("BR_PUBLIC_TRADESTATS_PROBE=" + json.dumps(item, ensure_ascii=False, sort_keys=True))

    return evidence


def main() -> int:
    validator = WalkForwardValidator.default()
    gold_resolution = _gold_resolution_evidence(validator)
    br_public_tradestats = _br_public_tradestats_evidence()

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
        "br_public_tradestats": br_public_tradestats,
        "reports": reports,
    }
    output = Path("artifacts/real_moex_validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

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
