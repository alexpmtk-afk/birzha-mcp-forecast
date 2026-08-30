from __future__ import annotations

import json
from pathlib import Path

from birzha.application.validation import WalkForwardValidator


CASES = (
    {"symbol": "SBER", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
    {"symbol": "Si", "start_date": "2026-04-01", "end_date": "2026-05-20", "step_sessions": 20, "max_points": 1},
)


def main() -> int:
    validator = WalkForwardValidator.default()
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
        "reports": reports,
    }
    output = Path("artifacts/real_moex_validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    # This gate proves only that the real historical path completes for both a
    # share and a futures root. It must never be interpreted as evidence of
    # statistical model quality; that requires a larger dedicated study.
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
