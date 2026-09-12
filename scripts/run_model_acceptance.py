from __future__ import annotations

import json
from pathlib import Path

from birzha.application.flow import MarketFlowService
from birzha.application.forecast import ForecastService
from birzha.application.market_data import MarketDataService
from birzha.application.model_lab import ModelAcceptanceService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.application.validation import WalkForwardValidator
from birzha.providers.moex_analytics import MoexAnalyticsClient
from birzha.providers.moex_calendar import MoexTradingCalendar


START_DATE = "2025-01-01"
END_DATE = "2026-05-31"
MAX_POINTS = 20
STEP_SESSIONS = 5


def _service() -> ModelAcceptanceService:
    control = ProcessUpstreamControlPlane()
    market = MarketDataService.default(control_plane=control)
    flow = MarketFlowService(
        market_data=market,
        analytics=MoexAnalyticsClient(control_plane=control),
    )
    snapshot = MarketSnapshotService(market_data=market, flow=flow)
    forecast = ForecastService(snapshots=snapshot)
    validator = WalkForwardValidator(
        market_data=market,
        forecasts=forecast,
        calendar=MoexTradingCalendar(market.provider),
    )
    return ModelAcceptanceService(validator=validator)


def main() -> None:
    service = _service()
    reports: list[dict[str, object]] = []
    for symbol in ("SBER", "Si"):
        report = service.assess(
            symbol,
            start_date=START_DATE,
            end_date=END_DATE,
            step_sessions=STEP_SESSIONS,
            max_points=MAX_POINTS,
        )
        payload = report.to_dict()
        reports.append(payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    artifact = {
        "schema": "BIRZHA_LEGACY_SBER_SI_SMOKE_V1",
        "purpose": "legacy transport/statistical smoke only",
        "quality_acceptance": False,
        "legacy": True,
        "warning": "must not be used as six-market model acceptance evidence",
        "start_date": START_DATE,
        "end_date": END_DATE,
        "max_points": MAX_POINTS,
        "step_sessions": STEP_SESSIONS,
        "reports": reports,
    }
    path = Path("artifacts/model_acceptance.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("LEGACY_SBER_SI_SMOKE=COMPUTED")


if __name__ == "__main__":
    main()
