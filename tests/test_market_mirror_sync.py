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
    def __init__(self, *, rows: int = 1) -> None:
        self.rows = rows
        self.written = None

    def health(self):
        return {"ok": True, "version": 2, "root_id": "root"}

    def ensure_archive(self, *, symbol: str):
        assert symbol == "BR"
        return {
            "spreadsheet_id": "sheet-br",
            "spreadsheet_name": "MOEX_HISTDATA_BR_MCP_CANONICAL",
            "folder_id": "br-folder",
            "folder_name": "BR — Brent",
            "created": True,
        }

    def replace_snapshot(self, *, spreadsheet_id: str, sheets):
        assert spreadsheet_id == "sheet-br"
        self.written = sheets
        return {"ok": True, "parity": True}

    def summary(self, *, spreadsheet_id: str):
        return {
            "ok": True,
            "sheets": {
                "D1": {
                    "data_rows": self.rows,
                    "first_data": ["key", "2024-03-01"],
                    "last_data": ["key", "2024-03-01"],
                }
            },
        }


def test_sync_resolves_archive_and_verifies_readback() -> None:
    bridge = Bridge()
    result = MarketMirrorSyncService(source=Source(), bridge=bridge).sync(
        symbol="BR", from_date="2024-03-01", till_date="2024-03-01"
    )
    assert result["status"] == "MIRROR_SYNC_PASS"
    assert result["folder_name"] == "BR — Brent"
    assert result["spreadsheet_id"] == "sheet-br"
    assert result["data_rows"] == 1
    assert bridge.written is not None


def test_sync_fails_closed_on_readback_row_mismatch() -> None:
    with pytest.raises(RuntimeError, match="row-count parity failed"):
        MarketMirrorSyncService(source=Source(), bridge=Bridge(rows=0)).sync(
            symbol="BR", from_date="2024-03-01", till_date="2024-03-01"
        )
