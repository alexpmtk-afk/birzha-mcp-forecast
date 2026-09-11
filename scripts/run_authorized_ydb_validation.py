from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import ydb

from birzha.application.calibration import ModelCalibrationService, calibrate_across_symbols
from birzha.application.flow import MarketFlowService
from birzha.application.forecast import ENGINE_VERSION, ForecastService
from birzha.application.historical_data import HistoricalDataService
from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.market_data import MarketDataService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.application.validation import WalkForwardValidator
from birzha.application.validation_capacity import stored_contract_capacity
from birzha.application.validation_readiness import (
    CORE_VALIDATION_SYMBOLS,
    ValidationDataReadinessService,
)
from birzha.providers.moex_analytics import MoexAnalyticsClient
from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.storage.ydb_historical_flow_store import YdbHistoricalFlowStore
from birzha.storage.ydb_historical_store import YdbHistoricalCandleStore
from birzha.storage.ydb_rate_gate import YdbSlotPacingGate
from birzha.storage.ydb_validation_governance import (
    HoldoutAlreadyConsumedError,
    YdbValidationGovernanceStore,
)


VALIDATION_PROTOCOL = "M23_HISTORICAL_GOVERNED_V1"
DEFAULT_DEVELOPMENT_START = "2021-01-01"
DEFAULT_SPLIT_DATE = "2022-12-31"
DEFAULT_HOLDOUT_END = "2024-12-31"
DEFAULT_MAX_POINTS = 80
MINIMUM_ACCEPTANCE_OBSERVATIONS = 20


def _token() -> str:
    value = subprocess.check_output(["yc", "iam", "create-token"], text=True).strip()
    if not value:
        raise RuntimeError("yc returned an empty IAM token")
    return value


def _write_artifact(path: str, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _period_capacity(
    history: HistoricalDataService,
    *,
    start_date: str,
    end_date: str,
    step_sessions: int,
    max_points: int,
) -> tuple[dict[str, object], dict[str, object]]:
    capacity_by_symbol: dict[str, object] = {}
    shortfall_by_symbol: dict[str, object] = {}
    for symbol in CORE_VALIDATION_SYMBOLS:
        item = stored_contract_capacity(
            history,
            symbol,
            from_date=start_date,
            till_date=end_date,
            step_sessions=step_sessions,
            max_points=max_points,
        )
        capacity_by_symbol[symbol] = item.to_dict()
        missing = {
            str(horizon): count
            for horizon, count in item.non_overlapping_observations.items()
            if count < MINIMUM_ACCEPTANCE_OBSERVATIONS
        }
        if missing:
            shortfall_by_symbol[symbol] = missing
    return capacity_by_symbol, shortfall_by_symbol


def _model_fingerprint(parameters: object, *, development_start: str, split_date: str) -> str:
    payload = {
        "validation_protocol": VALIDATION_PROTOCOL,
        "engine_version": ENGINE_VERSION,
        "symbols": list(CORE_VALIDATION_SYMBOLS),
        "development_start": development_start,
        "split_date": split_date,
        "parameters": parameters.to_dict(),  # type: ignore[attr-defined]
        "minimum_observations": MINIMUM_ACCEPTANCE_OBSERVATIONS,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed six-market model validation against authorized YDB history"
    )
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--development-start", default=DEFAULT_DEVELOPMENT_START)
    parser.add_argument("--split-date", default=DEFAULT_SPLIT_DATE)
    parser.add_argument("--holdout-end", default=DEFAULT_HOLDOUT_END)
    parser.add_argument("--step-sessions", type=int, default=5)
    parser.add_argument("--max-points", type=int, default=DEFAULT_MAX_POINTS)
    parser.add_argument(
        "--artifact", default="artifacts/ydb_model_validation.json"
    )
    args = parser.parse_args()

    if not args.development_start < args.split_date < args.holdout_end:
        raise ValueError("expected development_start < split_date < holdout_end")
    if args.step_sessions <= 0 or args.step_sessions > 50:
        raise ValueError("step_sessions must be between 1 and 50")
    if args.max_points <= 0 or args.max_points > 240:
        raise ValueError("max_points must be between 1 and 240")

    holdout_start = (
        date.fromisoformat(args.split_date[:10]) + timedelta(days=1)
    ).isoformat()

    driver = ydb.Driver(
        connection_string=args.connection_string,
        credentials=ydb.AccessTokenCredentials(_token()),
    )
    driver.wait(timeout=15, fail_fast=True)
    pool = ydb.QuerySessionPool(driver)
    try:
        control = ProcessUpstreamControlPlane(
            gate_factory=lambda provider_key: YdbSlotPacingGate(
                pool, provider_key=provider_key
            ),
            require_distributed_gate=True,
        )
        market = MarketDataService.default(control_plane=control)
        history = HistoricalDataService(
            market_data=market,
            store=YdbHistoricalCandleStore(pool),
        )
        readiness = ValidationDataReadinessService(history=history).check(
            CORE_VALIDATION_SYMBOLS,
            validation_start=args.development_start,
            validation_end=args.holdout_end,
        )
        artifact: dict[str, object] = {
            "validation_protocol": VALIDATION_PROTOCOL,
            "engine_version": ENGINE_VERSION,
            "development_start": args.development_start,
            "split_date": args.split_date,
            "holdout_start": holdout_start,
            "holdout_end": args.holdout_end,
            "step_sessions": args.step_sessions,
            "max_points": args.max_points,
            "symbols": list(CORE_VALIDATION_SYMBOLS),
            "readiness": readiness.to_dict(),
            "data_mode": "FROZEN_PREPARED_YDB",
            "minimum_acceptance_observations": MINIMUM_ACCEPTANCE_OBSERVATIONS,
        }
        if readiness.status != "READY":
            artifact["run_status"] = "DATA_NOT_READY"
            artifact["model_status"] = "NOT_EVALUATED"
            artifact["holdout_evaluated"] = False
            _write_artifact(args.artifact, artifact)
            print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
            return 2

        development_capacity, development_shortfall = _period_capacity(
            history,
            start_date=args.development_start,
            end_date=args.split_date,
            step_sessions=args.step_sessions,
            max_points=args.max_points,
        )
        holdout_capacity, holdout_shortfall = _period_capacity(
            history,
            start_date=holdout_start,
            end_date=args.holdout_end,
            step_sessions=args.step_sessions,
            max_points=args.max_points,
        )
        artifact["development_capacity"] = development_capacity
        artifact["holdout_capacity"] = holdout_capacity

        capacity_shortfall: dict[str, object] = {}
        if development_shortfall:
            capacity_shortfall["development"] = development_shortfall
        if holdout_shortfall:
            capacity_shortfall["holdout"] = holdout_shortfall

        if capacity_shortfall:
            artifact["run_status"] = "COMPUTED"
            artifact["model_status"] = "INSUFFICIENT_DATA"
            artifact["capacity_shortfall"] = capacity_shortfall
            artifact["development_statuses"] = {
                symbol: (
                    "INSUFFICIENT_SAMPLE"
                    if symbol in development_shortfall
                    else "NOT_EVALUATED"
                )
                for symbol in CORE_VALIDATION_SYMBOLS
            }
            artifact["holdout_evaluated"] = False
            artifact["holdout_statuses"] = {}
            artifact["calibration"] = None
            _write_artifact(args.artifact, artifact)
            print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
            return 0

        governance = YdbValidationGovernanceStore(pool)
        previous_claim = governance.overlapping_claim(holdout_start, args.holdout_end)
        if previous_claim is not None:
            artifact["run_status"] = "HOLDOUT_ALREADY_CONSUMED"
            artifact["model_status"] = "NOT_EVALUATED"
            artifact["holdout_evaluated"] = False
            artifact["holdout_claim"] = previous_claim.to_dict()
            artifact["calibration"] = None
            _write_artifact(args.artifact, artifact)
            print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
            return 0

        analytics = MoexAnalyticsClient(control_plane=control)
        historical_flow = HistoricalFlowDataService(
            market_data=market,
            analytics=analytics,
            store=YdbHistoricalFlowStore(pool),
            read_only=True,
        )
        flow = MarketFlowService(
            market_data=market,
            analytics=analytics,
            historical=historical_flow,
        )
        snapshot = MarketSnapshotService(market_data=market, flow=flow)
        forecast = ForecastService(snapshots=snapshot)
        validator = WalkForwardValidator(
            market_data=market,
            forecasts=forecast,
            calendar=MoexTradingCalendar(market.provider),
            history=history,
            historical_flow=historical_flow,
            prepare_history_before_run=False,
        )
        calibration = ModelCalibrationService(validator=validator)
        if calibration.minimum_observations != MINIMUM_ACCEPTANCE_OBSERVATIONS:
            raise RuntimeError(
                "capacity precheck minimum does not match model acceptance minimum"
            )

        claimed: dict[str, object] = {}

        def claim_holdout(parameters: object) -> None:
            fingerprint = _model_fingerprint(
                parameters,
                development_start=args.development_start,
                split_date=args.split_date,
            )
            claim = governance.claim_once(
                holdout_start=holdout_start,
                holdout_end=args.holdout_end,
                protocol=VALIDATION_PROTOCOL,
                engine_version=ENGINE_VERSION,
                model_fingerprint=fingerprint,
            )
            claimed.update(claim.to_dict())

        try:
            report = calibrate_across_symbols(
                calibration,
                CORE_VALIDATION_SYMBOLS,
                development_start=args.development_start,
                split_date=args.split_date,
                holdout_end=args.holdout_end,
                step_sessions=args.step_sessions,
                max_points=args.max_points,
                holdout_gate=claim_holdout,
            )
        except HoldoutAlreadyConsumedError:
            previous_claim = governance.overlapping_claim(
                holdout_start, args.holdout_end
            )
            artifact["run_status"] = "HOLDOUT_ALREADY_CONSUMED"
            artifact["model_status"] = "NOT_EVALUATED"
            artifact["holdout_evaluated"] = False
            artifact["holdout_claim"] = (
                previous_claim.to_dict() if previous_claim is not None else None
            )
            artifact["calibration"] = None
            _write_artifact(args.artifact, artifact)
            print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
            return 0

        selected_development = report.candidates[0].development
        artifact["run_status"] = "COMPUTED"
        artifact["model_status"] = report.status
        artifact["selected_parameters"] = report.selected.to_dict()
        artifact["development_statuses"] = {
            item.symbol: item.status for item in selected_development
        }
        artifact["holdout_evaluated"] = bool(report.holdout)
        artifact["holdout_statuses"] = {
            item.symbol: item.status for item in report.holdout
        }
        artifact["holdout_claim"] = claimed or None
        artifact["calibration"] = report.to_dict()
        _write_artifact(args.artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    finally:
        stop = getattr(pool, "stop", None) or getattr(pool, "close", None)
        if callable(stop):
            stop()
        driver.stop(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
