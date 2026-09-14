from __future__ import annotations

import argparse
import json
from pathlib import Path

import ydb

from birzha.application.market_mirror_sync import MarketMirrorSyncService
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge, GoogleSheetsBridgeConfig
from birzha.storage.ydb_runtime_storage import YdbRuntimeHistoricalCandleStore


def _read_secret(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"secret/token file is empty: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize verified persistent D1 history from YDB into the canonical "
            "Google Sheets archive for the requested market symbol."
        )
    )
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--till-date", required=True)
    parser.add_argument("--bridge-url", required=True)
    parser.add_argument("--bridge-secret-file", required=True)
    parser.add_argument("--root-folder-id", required=True)
    args = parser.parse_args()

    driver = ydb.Driver(
        connection_string=args.connection_string,
        credentials=ydb.AccessTokenCredentials(_read_secret(args.token_file)),
    )
    driver.wait(timeout=15, fail_fast=True)
    pool = ydb.QuerySessionPool(driver)
    try:
        store = YdbRuntimeHistoricalCandleStore(pool)
        bridge = GoogleSheetsBridge(
            GoogleSheetsBridgeConfig(
                bridge_url=args.bridge_url,
                bridge_secret=_read_secret(args.bridge_secret_file),
                root_folder_id=args.root_folder_id,
            )
        )
        result = MarketMirrorSyncService(source=store, bridge=bridge).sync(
            symbol=args.symbol,
            from_date=args.from_date,
            till_date=args.till_date,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    finally:
        pool.stop()
        driver.stop()


if __name__ == "__main__":
    raise SystemExit(main())
