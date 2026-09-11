from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import ydb

from birzha.application.historical_data import HistoricalDataService
from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.market_data import MarketDataService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.application.validation_readiness import (
    CORE_VALIDATION_SYMBOLS,
    ValidationDataReadinessService,
    required_price_ranges,
)
from birzha.providers.moex_analytics import MoexAnalyticsClient
from birzha.storage.ydb_historical_flow_store import YdbHistoricalFlowStore
from birzha.storage.ydb_historical_store import YdbHistoricalCandleStore
from birzha.storage.ydb_rate_gate import YdbSlotPacingGate


RETRY_DELAYS = (0, 5, 15)


def _token() -> str:
    value = subprocess.check_output(["yc", "iam", "create-token"], text=True).strip()
    if not value:
        raise RuntimeError("yc returned an empty IAM token")
    return value


def _write(path: str, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _retry(label: str, operation) -> tuple[bool, dict[str, object]]:
    last_error: Exception | None = None
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        try:
            result = operation()
            return True, {"label": label, "attempt": attempt, "result": result}
        except Exception as exc:
            last_error = exc
            print(
                json.dumps(
                    {
                        "status": "RETRY" if attempt < len(RETRY_DELAYS) else "ERROR",
                        "label": label,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
    assert last_error is not None
    return False, {
        "label": label,
        "error_type": type(last_error).__name__,
        "error": str(last_error)[:1000],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare exactly the YDB history required by six-market validation"
    )
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--validation-start", default="2025-01-01")
    parser.add_argument("--validation-end", default="2026-05-31")
    parser.add_argument(
        "--artifact", default="artifacts/ydb_validation_data_preparation.json"
    )
    args = parser.parse_args()

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
        analytics = MoexAnalyticsClient(control_plane=control)
        flow_history = HistoricalFlowDataService(
            market_data=market,
            analytics=analytics,
            store=YdbHistoricalFlowStore(pool),
        )

        operations: list[dict[str, object]] = []
        failures: list[dict[str, object]] = []
        for symbol in CORE_VALIDATION_SYMBOLS:
            for timeframe, left, right in required_price_ranges(
                args.validation_start, args.validation_end
            ):
                ok, payload = _retry(
                    f"price:{symbol}:{timeframe}",
                    lambda s=symbol, tf=timeframe, a=left, b=right: history.sync(
                        s, timeframe=tf, from_date=a, till_date=b
                    ).to_dict(),
                )
                operations.append(payload)
                if not ok:
                    failures.append(payload)

        flow_from = (
            date.fromisoformat(args.validation_start) - timedelta(days=10)
        ).isoformat()
        for symbol in CORE_VALIDATION_SYMBOLS:
            ok, payload = _retry(
                f"flow:{symbol}",
                lambda s=symbol: flow_history.sync(
                    s, from_date=flow_from, till_date=args.validation_end
                ),
            )
            operations.append(payload)
            if not ok:
                failures.append(payload)

        readiness = ValidationDataReadinessService(history=history).check(
            CORE_VALIDATION_SYMBOLS,
            validation_start=args.validation_start,
            validation_end=args.validation_end,
        )
        artifact: dict[str, object] = {
            "validation_start": args.validation_start,
            "validation_end": args.validation_end,
            "symbols": list(CORE_VALIDATION_SYMBOLS),
            "readiness": readiness.to_dict(),
            "operation_failures": failures,
            "operations": operations,
        }
        if readiness.status == "READY":
            artifact["status"] = "READY" if not failures else "READY_WITH_OPTIONAL_FLOW_ERRORS"
        else:
            artifact["status"] = "DATA_NOT_READY"
        _write(args.artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if readiness.status == "READY" else 2
    finally:
        stop = getattr(pool, "stop", None) or getattr(pool, "close", None)
        if callable(stop):
            stop()
        driver.stop(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
