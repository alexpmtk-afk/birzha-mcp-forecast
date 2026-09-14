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
    def replace_snapshot(
        self,
        *,
        spreadsheet_id: str,
        sheets: dict[str, list[list[object]]],
    ) -> dict[str, Any]: ...
    def summary(self, *, spreadsheet_id: str) -> dict[str, Any]: ...


@dataclass(slots=True)
class MarketMirrorSyncService:
    """Mirror verified persistent YDB D1 data; publish PASS status only last."""

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

        data_sheets = {
            name: snapshot.sheets[name]
            for name in ("D1", "SESSIONS", "VERIFIED_RANGES")
        }
        write_result = self.bridge.replace_snapshot(
            spreadsheet_id=spreadsheet_id,
            sheets=data_sheets,
        )
        if write_result.get("parity") is not True:
            raise RuntimeError("Google mirror data commit did not confirm parity")

        written = dict(write_result.get("sheets") or {})
        d1 = dict(written.get("D1") or {})
        if int(d1.get("data_rows") or -1) != snapshot.data_rows:
            raise RuntimeError(
                f"Google mirror row-count parity failed: {d1.get('data_rows')} != {snapshot.data_rows}"
            )
        d1_digest = str(d1.get("sha256") or "").strip().lower()
        if len(d1_digest) != 64:
            raise RuntimeError("Google mirror D1 post-commit digest is missing")

        # Human-facing PASS is deliberately committed only after all canonical
        # data sheets have independently passed post-commit digest/read-back.
        final_sync_status = [list(row) for row in snapshot.sheets["SYNC_STATUS"]]
        _set_sync_value(final_sync_status, "status", "MIRROR_SYNC_PASS")
        _set_sync_value(final_sync_status, "bridge_protocol", 1)
        _set_sync_value(final_sync_status, "bridge_release", health.get("bridge_release"))
        _set_sync_value(final_sync_status, "readback_row_count", snapshot.data_rows)
        _set_sync_value(final_sync_status, "d1_post_commit_sha256", d1_digest)

        status_result = self.bridge.replace_snapshot(
            spreadsheet_id=spreadsheet_id,
            sheets={"SYNC_STATUS": final_sync_status},
        )
        if status_result.get("parity") is not True:
            raise RuntimeError("Google mirror final status commit did not pass parity")

        # One final independent inspection confirms the status sheet exists and
        # that committing status did not alter the already committed D1 sheet.
        final_summary = self.bridge.summary(spreadsheet_id=spreadsheet_id)
        final_sheets = dict(final_summary.get("sheets") or {})
        final_d1 = dict(final_sheets.get("D1") or {})
        final_status = dict(final_sheets.get("SYNC_STATUS") or {})
        if int(final_d1.get("data_rows") or -1) != snapshot.data_rows:
            raise RuntimeError("Google mirror final D1 row-count changed after status commit")
        if str(final_d1.get("sha256") or "").strip().lower() != d1_digest:
            raise RuntimeError("Google mirror final D1 digest changed after status commit")
        if int(final_status.get("data_rows") or 0) < 1:
            raise RuntimeError("Google mirror final SYNC_STATUS is missing")

        return {
            "status": "MIRROR_SYNC_PASS",
            "symbol": snapshot.symbol,
            "from_date": snapshot.from_date,
            "till_date": snapshot.till_date,
            "data_rows": snapshot.data_rows,
            "contract_count": snapshot.contract_count,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": target.get("spreadsheet_name"),
            "folder_name": target.get("folder_name"),
            "archive_created": bool(target.get("created")),
            "bridge_protocol": 1,
            "bridge_release": health.get("bridge_release"),
            "bridge_parity": True,
            "d1_post_commit_sha256": d1_digest,
        }


def _set_sync_value(rows: list[list[object]], key: str, value: object) -> None:
    for row in rows[1:]:
        if len(row) >= 2 and str(row[0]) == key:
            row[1] = value
            return
    rows.append([key, value])
