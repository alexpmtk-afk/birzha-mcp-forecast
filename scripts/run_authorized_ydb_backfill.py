from __future__ import annotations

import argparse
import json
import subprocess
import time

import ydb

from birzha.application.historical_data import HistoricalDataService
from birzha.application.history_policy import require_persistent_price_timeframes
from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.storage.ydb_runtime_storage import (
    YdbRuntimeHistoricalCandleStore,
    YdbRuntimeSlotPacingGate,
)


CORE_SYMBOLS = ("SBER", "GOLD", "IMOEX", "RTSI", "Si", "BR")
RETRY_DELAYS = (0, 5, 15)


def _csv(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("value must contain at least one item")
    return items


def _token() -> str:
    value = subprocess.check_output(["yc", "iam", "create-token"], text=True).strip()
    if not value:
        raise RuntimeError("yc returned an empty IAM token")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Authorized resumable D1-only YDB history backfill"
    )
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--till-date", required=True)
    parser.add_argument("--symbols", type=_csv, default=CORE_SYMBOLS)
    parser.add_argument(
        "--timeframes",
        type=_csv,
        default=("D1",),
        help="Durable history is D1-only; H1/M15 must be fetched on demand.",
    )
    args = parser.parse_args()
    timeframes = require_persistent_price_timeframes(args.timeframes)

    driver = ydb.Driver(
        connection_string=args.connection_string,
        credentials=ydb.AccessTokenCredentials(_token()),
    )
    driver.wait(timeout=15, fail_fast=True)
    pool = ydb.QuerySessionPool(driver)
    control = ProcessUpstreamControlPlane(
        gate_factory=lambda provider_key: YdbRuntimeSlotPacingGate(
            pool, provider_key=provider_key
        ),
        require_distributed_gate=True,
    )
    history = HistoricalDataService(
        market_data=MarketDataService.default(control_plane=control),
        store=YdbRuntimeHistoricalCandleStore(pool),
    )

    failures: list[dict[str, str]] = []
    try:
        for symbol in args.symbols:
            for timeframe in timeframes:
                for attempt, delay in enumerate(RETRY_DELAYS, start=1):
                    if delay:
                        time.sleep(delay)
                    started = time.monotonic()
                    try:
                        result = history.sync(
                            symbol,
                            timeframe=timeframe,
                            from_date=args.from_date,
                            till_date=args.till_date,
                        )
                        if is_futures_root_symbol(symbol):
                            empty_contracts = tuple(
                                item.secid for item in result.contracts if item.stored_candles == 0
                            )
                            if empty_contracts:
                                raise RuntimeError(
                                    "rolling futures sync produced empty contract segments: "
                                    + ",".join(empty_contracts)
                                )
                    except Exception as exc:
                        event = {
                            "status": "RETRY" if attempt < len(RETRY_DELAYS) else "ERROR",
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "attempt": str(attempt),
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:1000],
                        }
                        print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
                        if attempt == len(RETRY_DELAYS):
                            failures.append(event)
                        continue
                    payload = {
                        "status": "PASS",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "attempt": attempt,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        **result.to_dict(),
                    }
                    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
                    break
    finally:
        stop = getattr(pool, "stop", None) or getattr(pool, "close", None)
        if callable(stop):
            stop()
        driver.stop(timeout=5)

    print(
        json.dumps(
            {
                "status": "PASS" if not failures else "PARTIAL",
                "persistent_timeframes": list(timeframes),
                "intraday_mode": "ON_DEMAND_NOT_PERSISTED",
                "failures": failures,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
