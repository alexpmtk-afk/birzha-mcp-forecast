from __future__ import annotations

import argparse
import json
from pathlib import Path

import ydb

from birzha.application.market_mirror import build_market_mirror_snapshot
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge, GoogleSheetsBridgeConfig
from birzha.storage.ydb_runtime_storage import YdbRuntimeHistoricalCandleStore


def _read_secret(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"secret/token file is empty: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize verified persistent D1 history from YDB to the bounded Google Sheets mirror."
    )
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--till-date", required=True)
    parser.add_argument("--spreadsheet-id", required=True)
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
        snapshot = build_market_mirror_snapshot(
            store,
            symbol=args.symbol,
            from_date=args.from_date,
            till_date=args.till_date,
        )
        bridge = GoogleSheetsBridge(
            GoogleSheetsBridgeConfig(
                bridge_url=args.bridge_url,
                bridge_secret=_read_secret(args.bridge_secret_file),
                root_folder_id=args.root_folder_id,
            )
        )
        health = bridge.health()
        write_result = bridge.replace_snapshot(
            spreadsheet_id=args.spreadsheet_id,
            sheets=snapshot.sheets,
        )
        summary = bridge.summary(spreadsheet_id=args.spreadsheet_id)
        d1 = dict(summary.get("sheets", {}).get("D1") or {})
        if int(d1.get("data_rows") or -1) != snapshot.data_rows:
            raise RuntimeError(
                f"Google mirror row-count parity failed: {d1.get('data_rows')} != {snapshot.data_rows}"
            )
        first_data = list(d1.get("first_data") or [])
        last_data = list(d1.get("last_data") or [])
        if len(first_data) < 2 or str(first_data[1])[:10] != snapshot.from_date:
            raise RuntimeError("Google mirror first-date parity failed")
        if len(last_data) < 2 or str(last_data[1])[:10] != snapshot.till_date:
            raise RuntimeError("Google mirror last-date parity failed")

        result = {
            "status": "MIRROR_SYNC_PASS",
            "symbol": snapshot.symbol,
            "from_date": snapshot.from_date,
            "till_date": snapshot.till_date,
            "data_rows": snapshot.data_rows,
            "contract_count": snapshot.contract_count,
            "spreadsheet_id": args.spreadsheet_id,
            "bridge_version": health.get("version"),
            "bridge_parity": write_result.get("parity"),
            "readback_row_count": d1.get("data_rows"),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    finally:
        pool.stop()
        driver.stop()


if __name__ == "__main__":
    raise SystemExit(main())
