"""Fail-closed synchronization of verified YDB D1 history through Bridge v1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from birzha.application.market_mirror import (
    MarketMirrorDataSource,
    MarketMirrorSnapshot,
    build_market_mirror_snapshot,
)


@dataclass(frozen=True, slots=True)
class MarketMirrorTarget:
    path: str
    filename: str
    spreadsheet_id: str = ""


# Archive mapping is Birzha business configuration and deliberately does not
# live in the shared Google Drive Bridge implementation.
MARKET_MIRROR_TARGETS: dict[str, MarketMirrorTarget] = {
    "SI": MarketMirrorTarget("Si — Доллар-рубль", "MOEX_HISTDATA_Si_MCP_CANONICAL"),
    "BR": MarketMirrorTarget(
        "BR — Brent",
        "BIRZHA — BR — Market Data Mirror",
        "1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ",
    ),
    "GOLD": MarketMirrorTarget("GOLD — Золото", "MOEX_HISTDATA_GOLD_MCP_CANONICAL"),
    "IMOEX": MarketMirrorTarget(
        "MIX_MX — Индекс МосБиржи", "MOEX_HISTDATA_MIX_MX_MCP_CANONICAL"
    ),
    "RTSI": MarketMirrorTarget("RTS — Индекс РТС", "MOEX_HISTDATA_RTS_MCP_CANONICAL"),
    "SBER": MarketMirrorTarget("SBER — Сбербанк", "MOEX_HISTDATA_SBER_MCP_CANONICAL"),
    "TATN": MarketMirrorTarget("TATN — Татнефть", "MOEX_HISTDATA_TATN_MCP_CANONICAL"),
}


class MarketMirrorBridge(Protocol):
    def health(self) -> dict[str, Any]: ...

    def sheet_ensure(
        self,
        *,
        path: str,
        filename: str,
        spreadsheet_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def sheet_stage_begin(
        self,
        *,
        spreadsheet_id: str,
        sheet_title: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def sheet_write_chunk(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        start_row: int,
        start_col: int,
        values: list[list[object]],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def sheet_verify(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        date_column: int = 0,
    ) -> dict[str, Any]: ...

    def sheet_commit(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        target_sheet_title: str,
        expected_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def sheet_abort(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class MarketMirrorSyncService:
    source: MarketMirrorDataSource
    bridge: MarketMirrorBridge
    chunk_rows: int = 500

    def __post_init__(self) -> None:
        if self.chunk_rows < 1 or self.chunk_rows > 1000:
            raise ValueError("market mirror chunk_rows must be 1..1000")

    def sync(self, *, symbol: str, from_date: str, till_date: str) -> dict[str, object]:
        snapshot = build_market_mirror_snapshot(
            self.source,
            symbol=symbol,
            from_date=from_date,
            till_date=till_date,
        )
        target = _target_for(snapshot.symbol)
        health = self.bridge.health()
        base_key = _snapshot_key(snapshot)

        ensured = self.bridge.sheet_ensure(
            path=target.path,
            filename=target.filename,
            spreadsheet_id=target.spreadsheet_id,
            idempotency_key=f"{base_key}:ensure",
        )
        spreadsheet = ensured.get("spreadsheet") if isinstance(ensured.get("spreadsheet"), dict) else {}
        spreadsheet_id = str(spreadsheet.get("id") or "").strip()
        if not spreadsheet_id:
            raise RuntimeError("Bridge v1 sheet_ensure returned no spreadsheet id")

        sheets = {name: [list(row) for row in rows] for name, rows in snapshot.sheets.items()}
        # PASS lives only in a hidden stage until every sheet has passed read-back
        # verification. SYNC_STATUS is committed last.
        final_status = sheets["SYNC_STATUS"]
        _set_sync_value(final_status, "status", "MIRROR_SYNC_PASS")
        _set_sync_value(final_status, "bridge_protocol_version", 1)
        _set_sync_value(final_status, "bridge_release", health.get("bridge_release"))
        _set_sync_value(final_status, "readback_row_count", snapshot.data_rows)

        stage_order = ("D1", "SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS")
        stages: dict[str, str] = {}
        verified: dict[str, dict[str, Any]] = {}
        committed: set[str] = set()
        try:
            for sheet_name in stage_order:
                rows = sheets[sheet_name]
                staged = self.bridge.sheet_stage_begin(
                    spreadsheet_id=spreadsheet_id,
                    sheet_title=sheet_name,
                    idempotency_key=f"{base_key}:{sheet_name}:stage",
                )
                stage_title = str(staged.get("stage_sheet_title") or "").strip()
                if not stage_title:
                    raise RuntimeError(f"Bridge v1 returned no stage title for {sheet_name}")
                stages[sheet_name] = stage_title

                for index, offset in enumerate(range(0, len(rows), self.chunk_rows)):
                    chunk = rows[offset : offset + self.chunk_rows]
                    self.bridge.sheet_write_chunk(
                        spreadsheet_id=spreadsheet_id,
                        stage_sheet_title=stage_title,
                        start_row=offset + 1,
                        start_col=1,
                        values=chunk,
                        idempotency_key=f"{base_key}:{sheet_name}:chunk:{index}",
                    )

                # D1 date column is 1-based column 2. Bridge v1 alpha reports the
                # header marker as first_date, so exact content is protected by
                # the stage digest while the final data date is checked here.
                check = self.bridge.sheet_verify(
                    spreadsheet_id=spreadsheet_id,
                    stage_sheet_title=stage_title,
                    date_column=2 if sheet_name == "D1" else 0,
                )
                _verify_stage(sheet_name, rows, snapshot, check)
                verified[sheet_name] = check

            # Commit status last so a partial multi-sheet failure cannot claim PASS.
            for sheet_name in stage_order:
                check = verified[sheet_name]
                result = self.bridge.sheet_commit(
                    spreadsheet_id=spreadsheet_id,
                    stage_sheet_title=stages[sheet_name],
                    target_sheet_title=sheet_name,
                    expected_digest=str(check["digest"]),
                    idempotency_key=f"{base_key}:{sheet_name}:commit",
                )
                if result.get("committed") is not True:
                    raise RuntimeError(f"Bridge v1 did not commit {sheet_name}")
                committed.add(sheet_name)
        except Exception:
            # Abort every uncommitted stage best-effort. Per-sheet commit itself
            # provides rollback of the canonical tab name; SYNC_STATUS is last.
            for sheet_name, stage_title in stages.items():
                if sheet_name in committed:
                    continue
                try:
                    self.bridge.sheet_abort(
                        spreadsheet_id=spreadsheet_id,
                        stage_sheet_title=stage_title,
                        idempotency_key=f"{base_key}:{sheet_name}:abort",
                    )
                except Exception:
                    pass
            raise

        return {
            "status": "MIRROR_SYNC_PASS",
            "symbol": snapshot.symbol,
            "from_date": snapshot.from_date,
            "till_date": snapshot.till_date,
            "data_rows": snapshot.data_rows,
            "contract_count": snapshot.contract_count,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": spreadsheet.get("name") or target.filename,
            "archive_path": target.path,
            "archive_created": bool(ensured.get("created")),
            "bridge_protocol_version": 1,
            "bridge_release": health.get("bridge_release"),
            "bridge_parity": True,
            "readback_row_count": snapshot.data_rows,
        }


def _target_for(symbol: str) -> MarketMirrorTarget:
    key = symbol.strip().upper()
    try:
        return MARKET_MIRROR_TARGETS[key]
    except KeyError as exc:
        raise ValueError(f"unsupported Birzha market mirror symbol: {symbol}") from exc


def _snapshot_key(snapshot: MarketMirrorSnapshot) -> str:
    raw = json.dumps(snapshot.sheets, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"birzha-mirror:{snapshot.symbol}:{snapshot.from_date}:{snapshot.till_date}:{digest}"


def _verify_stage(
    sheet_name: str,
    rows: list[list[object]],
    snapshot: MarketMirrorSnapshot,
    check: dict[str, Any],
) -> None:
    expected_rows = len(rows)
    expected_columns = len(rows[0]) if rows else 0
    if int(check.get("row_count") or -1) != expected_rows:
        raise RuntimeError(
            f"Bridge v1 {sheet_name} row-count parity failed: "
            f"{check.get('row_count')} != {expected_rows}"
        )
    if int(check.get("column_count") or -1) != expected_columns:
        raise RuntimeError(
            f"Bridge v1 {sheet_name} column-count parity failed: "
            f"{check.get('column_count')} != {expected_columns}"
        )
    digest = str(check.get("digest") or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise RuntimeError(f"Bridge v1 {sheet_name} returned invalid stage digest")
    if sheet_name == "D1":
        if str(check.get("first_date") or "") != str(rows[0][1]):
            raise RuntimeError("Bridge v1 D1 first-date/header marker parity failed")
        if str(check.get("last_date") or "")[:10] != snapshot.till_date:
            raise RuntimeError("Bridge v1 D1 last-date parity failed")


def _set_sync_value(rows: list[list[object]], key: str, value: object) -> None:
    for row in rows[1:]:
        if len(row) >= 2 and str(row[0]) == key:
            row[1] = value
            return
    rows.append([key, value])
