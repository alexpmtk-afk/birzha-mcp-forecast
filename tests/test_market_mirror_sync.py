from __future__ import annotations

import pytest

from birzha.application.market_mirror_sync import MarketMirrorSyncService
from birzha.domain.market import Candle, CandleSeries, Instrument


class Source:
    def __init__(self) -> None:
        self.instrument = Instrument(
            symbol="BR", secid="BRH4", board="RFUD", engine="futures",
            market="forts", asset_class="future", root_symbol="BR",
        )

    def stored_session_contracts(self, symbol: str, from_date: str, till_date: str):
        return (("2024-03-01", "BRH4"),)

    def stored_instrument(self, secid: str):
        return self.instrument if secid == "BRH4" else None

    def read(self, instrument: Instrument, timeframe: str, from_date: str, till_date: str):
        return CandleSeries(
            instrument=instrument,
            timeframe="D1",
            candles=(
                Candle(
                    82.0, 82.5, 83.0, 81.5, 1000.0, 10.0,
                    "2024-03-01 00:00:00", "2024-03-01 23:59:59",
                ),
            ),
            source="MOEX_ISS",
        )

    def is_session_range_verified(self, symbol: str, from_date: str, till_date: str):
        return True


class Bridge:
    def __init__(self, *, fail_sheet: str | None = None) -> None:
        self.fail_sheet = fail_sheet
        self.ensure_calls = []
        self.stage_calls = []
        self.chunk_calls = []
        self.verify_calls = []
        self.commit_calls = []
        self.abort_calls = []
        self.rows_by_stage = {}

    def health(self):
        return {
            "protocol_version": 1,
            "bridge_release": "1.0.0-alpha.1",
            "project_id": "birzha",
            "root_id": "root",
        }

    def sheet_ensure(self, **kwargs):
        self.ensure_calls.append(kwargs)
        return {
            "spreadsheet": {
                "id": "sheet-br",
                "name": "BIRZHA — BR — Market Data Mirror",
            },
            "created": False,
        }

    def sheet_stage_begin(self, **kwargs):
        self.stage_calls.append(kwargs)
        title = kwargs["sheet_title"]
        stage = f"stage-{title}"
        self.rows_by_stage[stage] = []
        return {"stage_sheet_title": stage}

    def sheet_write_chunk(self, **kwargs):
        self.chunk_calls.append(kwargs)
        stage = kwargs["stage_sheet_title"]
        if self.fail_sheet and stage == f"stage-{self.fail_sheet}":
            raise RuntimeError(f"forced chunk failure for {self.fail_sheet}")
        rows = self.rows_by_stage[stage]
        start = kwargs["start_row"] - 1
        while len(rows) < start:
            rows.append([])
        values = [list(row) for row in kwargs["values"]]
        rows[start:start + len(values)] = values
        return {"written_rows": len(values)}

    def sheet_verify(self, **kwargs):
        self.verify_calls.append(kwargs)
        rows = self.rows_by_stage[kwargs["stage_sheet_title"]]
        result = {
            "row_count": len(rows),
            "column_count": len(rows[0]),
            "digest": "a" * 64,
        }
        if kwargs.get("date_column") == 2:
            result["first_date"] = rows[0][1]
            result["last_date"] = rows[-1][1]
        return result

    def sheet_commit(self, **kwargs):
        self.commit_calls.append(kwargs)
        return {"committed": True}

    def sheet_abort(self, **kwargs):
        self.abort_calls.append(kwargs)
        return {"aborted": True}


def _sync() -> tuple[MarketMirrorSyncService, Bridge]:
    bridge = Bridge()
    return MarketMirrorSyncService(source=Source(), bridge=bridge, chunk_rows=1), bridge


def test_sync_uses_prebound_br_target_and_commits_status_last() -> None:
    service, bridge = _sync()
    result = service.sync(symbol="BR", from_date="2024-03-01", till_date="2024-03-01")

    assert result["status"] == "MIRROR_SYNC_PASS"
    assert result["spreadsheet_id"] == "sheet-br"
    assert result["archive_path"] == "BR — Brent"
    assert result["bridge_protocol_version"] == 1
    assert bridge.ensure_calls[0]["spreadsheet_id"] == "1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ"
    assert bridge.ensure_calls[0]["path"] == "BR — Brent"
    assert bridge.ensure_calls[0]["filename"] == "BIRZHA — BR — Market Data Mirror"
    assert [call["target_sheet_title"] for call in bridge.commit_calls] == [
        "D1", "SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS"
    ]
    assert bridge.abort_calls == []


def test_sync_chunks_rows_and_uses_stable_operation_keys() -> None:
    service, bridge = _sync()
    service.sync(symbol="BR", from_date="2024-03-01", till_date="2024-03-01")

    d1_chunks = [c for c in bridge.chunk_calls if c["stage_sheet_title"] == "stage-D1"]
    assert len(d1_chunks) == 2  # header + one D1 row, chunk_rows=1
    assert d1_chunks[0]["start_row"] == 1
    assert d1_chunks[1]["start_row"] == 2
    assert d1_chunks[0]["idempotency_key"].endswith(":D1:chunk:0")
    assert d1_chunks[1]["idempotency_key"].endswith(":D1:chunk:1")
    assert bridge.stage_calls[0]["idempotency_key"].endswith(":D1:stage")
    assert bridge.commit_calls[0]["idempotency_key"].endswith(":D1:commit")


def test_sync_verifies_d1_rows_digest_and_last_date_before_commit() -> None:
    service, bridge = _sync()
    service.sync(symbol="BR", from_date="2024-03-01", till_date="2024-03-01")

    d1_verify = next(c for c in bridge.verify_calls if c["stage_sheet_title"] == "stage-D1")
    assert d1_verify["date_column"] == 2
    assert len(bridge.verify_calls) == 4
    assert len(bridge.commit_calls) == 4


def test_sync_aborts_uncommitted_stages_on_mid_write_failure() -> None:
    bridge = Bridge(fail_sheet="SESSIONS")
    service = MarketMirrorSyncService(source=Source(), bridge=bridge, chunk_rows=10)

    with pytest.raises(RuntimeError, match="forced chunk failure"):
        service.sync(symbol="BR", from_date="2024-03-01", till_date="2024-03-01")

    assert bridge.commit_calls == []
    aborted = {call["stage_sheet_title"] for call in bridge.abort_calls}
    assert aborted == {"stage-D1", "stage-SESSIONS"}


def test_sync_status_pass_is_only_in_hidden_stage_before_commit() -> None:
    service, bridge = _sync()
    service.sync(symbol="BR", from_date="2024-03-01", till_date="2024-03-01")

    status_rows = bridge.rows_by_stage["stage-SYNC_STATUS"]
    values = {row[0]: row[1] for row in status_rows[1:] if len(row) >= 2}
    assert values["status"] == "MIRROR_SYNC_PASS"
    assert values["bridge_protocol_version"] == 1
    assert values["bridge_release"] == "1.0.0-alpha.1"
    assert bridge.commit_calls[-1]["target_sheet_title"] == "SYNC_STATUS"


def test_unsupported_symbol_fails_before_drive_mutation() -> None:
    bridge = Bridge()
    service = MarketMirrorSyncService(source=Source(), bridge=bridge)
    with pytest.raises(ValueError, match="unsupported Birzha market mirror symbol"):
        service.sync(symbol="UNKNOWN", from_date="2024-03-01", till_date="2024-03-01")
    assert bridge.ensure_calls == []
