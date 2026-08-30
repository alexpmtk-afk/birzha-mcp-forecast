from __future__ import annotations

import json
from pathlib import Path

from birzha.application.validation import WalkForwardValidator


CASES = (
    {"symbol": "SBER", "start_date": "2026-04-01", "end_date": "2026-06-30", "step_sessions": 20, "max_points": 2},
    {"symbol": "Si", "start_date": "2026-04-01", "end_date": "2026-06-30", "step_sessions": 20, "max_points": 2},
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
        "purpose": "bounded real-network causal walk-forward smoke evidence",
        "reports": reports,
    }
    output = Path("artifacts/real_moex_validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    # This gate proves the validation path can complete on real MOEX data. It is
    # deliberately not a model-quality acceptance threshold yet: calibration is
    # decided only after a larger historical study.
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
