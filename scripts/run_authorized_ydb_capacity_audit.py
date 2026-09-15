from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import ydb

from birzha.application.historical_data import HistoricalDataService
from birzha.application.market_data import MarketDataService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.application.validation_capacity import stored_contract_capacity
from birzha.application.validation_readiness import (
    CORE_VALIDATION_SYMBOLS,
    ValidationDataReadinessService,
)
from birzha.storage.ydb_runtime_storage import (
    YdbRuntimeHistoricalCandleStore,
    YdbRuntimeSlotPacingGate,
)


VALIDATION_PROTOCOL = "M23_HISTORICAL_GOVERNED_V1"
DEFAULT_DEVELOPMENT_START = "2021-01-01"
DEFAULT_SPLIT_DATE = "2022-12-31"
DEFAULT_HOLDOUT_END = "2024-12-31"
DEFAULT_MAX_POINTS = 80
MINIMUM_ACCEPTANCE_OBSERVATIONS = 20


def _read_token(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("token file is empty")
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


def _finalize_capacity_artifact(
    artifact: dict[str, object],
    *,
    development_shortfall: dict[str, object],
    holdout_shortfall: dict[str, object],
) -> dict[str, object]:
    capacity_shortfall: dict[str, object] = {}
    if development_shortfall:
        capacity_shortfall["development"] = development_shortfall
    if holdout_shortfall:
        capacity_shortfall["holdout"] = holdout_shortfall

    artifact["run_status"] = (
        "INSUFFICIENT_DATA" if capacity_shortfall else "CAPACITY_SUFFICIENT"
    )
    artifact["model_status"] = "NOT_EVALUATED"
    artifact["holdout_evaluated"] = False
    artifact["holdout_sealed"] = True
    artifact["capacity_shortfall"] = capacity_shortfall
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only YDB capacity audit. Counts governed independent samples "
            "without model evaluation and without opening the holdout."
        )
    )
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--development-start", default=DEFAULT_DEVELOPMENT_START)
    parser.add_argument("--split-date", default=DEFAULT_SPLIT_DATE)
    parser.add_argument("--holdout-end", default=DEFAULT_HOLDOUT_END)
    parser.add_argument("--step-sessions", type=int, default=5)
    parser.add_argument("--max-points", type=int, default=DEFAULT_MAX_POINTS)
    parser.add_argument(
        "--artifact", default="artifacts/ydb_capacity_audit.json"
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
        credentials=ydb.AccessTokenCredentials(_read_token(args.token_file)),
    )
    driver.wait(timeout=15, fail_fast=True)
    pool = ydb.QuerySessionPool(driver)
    try:
        control = ProcessUpstreamControlPlane(
            gate_factory=lambda provider_key: YdbRuntimeSlotPacingGate(
                pool, provider_key=provider_key
            ),
            require_distributed_gate=True,
        )
        market = MarketDataService.default(control_plane=control)
        history = HistoricalDataService(
            market_data=market,
            store=YdbRuntimeHistoricalCandleStore(pool),
        )
        readiness = ValidationDataReadinessService(history=history).check(
            CORE_VALIDATION_SYMBOLS,
            validation_start=args.development_start,
            validation_end=args.holdout_end,
        )
        artifact: dict[str, object] = {
            "validation_protocol": VALIDATION_PROTOCOL,
            "audit_mode": "CAPACITY_ONLY_READ_ONLY",
            "development_start": args.development_start,
            "split_date": args.split_date,
            "holdout_start": holdout_start,
            "holdout_end": args.holdout_end,
            "step_sessions": args.step_sessions,
            "max_points": args.max_points,
            "symbols": list(CORE_VALIDATION_SYMBOLS),
            "minimum_acceptance_observations": MINIMUM_ACCEPTANCE_OBSERVATIONS,
            "readiness": readiness.to_dict(),
            "persistent_price_timeframes": ["D1"],
            "intraday_mode": "ON_DEMAND_NOT_PERSISTED",
            "model_status": "NOT_EVALUATED",
            "holdout_evaluated": False,
            "holdout_sealed": True,
        }
        if readiness.status != "READY":
            artifact["run_status"] = "DATA_NOT_READY"
            artifact["capacity_shortfall"] = {}
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
        _finalize_capacity_artifact(
            artifact,
            development_shortfall=development_shortfall,
            holdout_shortfall=holdout_shortfall,
        )
        _write_artifact(args.artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    finally:
        pool.stop()
        driver.stop()


if __name__ == "__main__":
    raise SystemExit(main())
