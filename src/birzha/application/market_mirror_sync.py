"""Fail-closed synchronization of verified YDB D1 history to Google Sheets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from birzha.application.market_mirror import (
    MarketMirrorDataSource,
    build_market_mirror_snapshot,
)


class MarketMirrorBridge(Protocol):
    def health(self) -> dict[str, Any]: ...
    def ensure_archive(self, *, symbol: str) -> dict[str, Any]: ...
    def replace_snapshot(self, *, spreadsheet_id: str, sheets: dict[str, list[list[object]]]) -> dict[str, Any]: ...
    def summary(self, *, spreadsheet_id: str) -> dict[str, Any]: ...


@dataclass(slots=True)
class MarketMirrorSyncService:
    source: MarketMirrorDataSource
    bridge: MarketMirrorBridge

    def sync(self, *, symbol: str, from_date: str, till_date: str) -> dict[str, object]:
        snapshot = build_market_mirror_snapshot(
            self.source,
            symbol=symbol,
            from_date=from_date,
            till_date=till_date,
        )
        health = self.bridge.health()
        target = self.bridge.ensure_archive(symbol=snapshot.symbol)
        spreadsheet_id = str(target.get("spreadsheet_id") or "").strip()
        if not spreadsheet_id:
            raise RuntimeError("Google mirror target has no spreadsheet_id")

        write_result = self.bridge.replace_snapshot(
            spreadsheet_id=spreadsheet_id,
            sheets=snapshot.sheets,
        )
        if write_result.get("parity") is not True:
            raise RuntimeError("Google mirror bridge did not confirm write parity")

        summary = self.bridge.summary(spreadsheet_id=spreadsheet_id)
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

        # Only after the independent read-back checks pass may the human-facing
        # SYNC_STATUS sheet claim PASS. A failed write/readback therefore never
        # leaves a false successful status in Google Sheets.
        final_sync_status = [list(row) for row in snapshot.sheets["SYNC_STATUS"]]
        _set_sync_value(final_sync_status, "status", "MIRROR_SYNC_PASS")
        _set_sync_value(final_sync_status, "bridge_version", health.get("version"))
        _set_sync_value(final_sync_status, "readback_row_count", d1.get("data_rows"))
        final_status_result = self.bridge.replace_snapshot(
            spreadsheet_id=spreadsheet_id,
            sheets={"SYNC_STATUS": final_sync_status},
        )
        if final_status_result.get("parity") is not True:
            raise RuntimeError("Google mirror final status write did not pass parity")

        return {
            "status": "MIRROR_SYNC_PASS",
            "symbol": snapshot.symbol,
            "from_date": snapshot.from_date,
            "till_date": snapshot.till_date,
            "data_rows": snapshot.data_rows,
            "contract_count": snapshot.contract_count,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": target.get("spreadsheet_name"),
            "folder_id": target.get("folder_id"),
            "folder_name": target.get("folder_name"),
            "archive_created": bool(target.get("created")),
            "bridge_version": health.get("version"),
            "bridge_parity": True,
            "readback_row_count": d1.get("data_rows"),
        }


def _set_sync_value(rows: list[list[object]], key: str, value: object) -> None:
    for row in rows[1:]:
        if len(row) >= 2 and str(row[0]) == key:
            row[1] = value
            return
    rows.append([key, value])
