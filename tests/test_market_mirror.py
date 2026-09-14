from __future__ import annotations

import pytest

from birzha.application.historical_data import _verification_symbol
from birzha.application.market_data import is_futures_root_symbol
from birzha.application.market_mirror import build_market_mirror_snapshot
from birzha.domain.market import Candle, CandleSeries, Instrument


class FakeSource:
    def __init__(self, *, omit_second: bool = False, include_overlap: bool = False) -> None:
        self.omit_second = omit_second
        self.include_overlap = include_overlap
        self.instruments = {
            "BRH4": Instrument(
                symbol="BR", secid="BRH4", board="RFUD", engine="futures",
                market="forts", asset_class="future", root_symbol="BR",
            ),
            "BRJ4": Instrument(
                symbol="BR", secid="BRJ4", board="RFUD", engine="futures",
                market="forts", asset_class="future", root_symbol="BR",
            ),
        }

    def stored_session_contracts(self, symbol: str, from_date: str, till_date: str):
        expected = _verification_symbol("BR", "D1", is_root=is_futures_root_symbol("BR"))
        assert symbol == expected
        return (("2024-03-01", "BRH4"), ("2024-03-04", "BRJ4"))

    def stored_instrument(self, secid: str):
        return self.instruments.get(secid)

    def read(self, instrument: Instrument, timeframe: str, from_date: str, till_date: str):
        assert timeframe == "D1"
        if instrument.secid == "BRH4":
            candles = [
                Candle(82.0, 82.5, 83.0, 81.5, 1000.0, 10.0, "2024-03-01 00:00:00", "2024-03-01 23:59:59"),
            ]
            if self.include_overlap:
                candles.append(
                    Candle(82.2, 82.7, 83.1, 81.8, 900.0, 9.0, "2024-03-04 00:00:00", "2024-03-04 23:59:59")
                )
        elif self.omit_second:
            candles = []
        else:
            candles = [
                Candle(82.6, 83.1, 83.4, 82.2, 1200.0, 12.0, "2024-03-04 00:00:00", "2024-03-04 23:59:59"),
            ]
            if self.include_overlap:
                candles.insert(
                    0,
                    Candle(82.1, 82.4, 82.8, 81.7, 850.0, 8.0, "2024-03-01 00:00:00", "2024-03-01 23:59:59"),
                )
        return CandleSeries(instrument=instrument, timeframe="D1", candles=tuple(candles), source="MOEX_ISS")

    def is_session_range_verified(self, symbol: str, from_date: str, till_date: str):
        return True


def test_build_market_mirror_snapshot_matches_sessions() -> None:
    snapshot = build_market_mirror_snapshot(
        FakeSource(), symbol="BR", from_date="2024-03-01", till_date="2024-03-04"
    )
    assert snapshot.data_rows == 2
    assert snapshot.contract_count == 2
    assert snapshot.from_date == "2024-03-01"
    assert snapshot.till_date == "2024-03-04"
    assert snapshot.sheets["D1"][1][8] == "BRH4"
    assert snapshot.sheets["D1"][2][8] == "BRJ4"
    assert snapshot.sheets["SESSIONS"] == [
        ["symbol", "secid", "trade_date"],
        ["BR", "BRH4", "2024-03-01"],
        ["BR", "BRJ4", "2024-03-04"],
    ]


def test_build_market_mirror_snapshot_preserves_overlapping_contract_rows() -> None:
    snapshot = build_market_mirror_snapshot(
        FakeSource(include_overlap=True),
        symbol="BR",
        from_date="2024-03-01",
        till_date="2024-03-04",
    )
    assert snapshot.data_rows == 4
    rows = snapshot.sheets["D1"][1:]
    assert [(row[1], row[8]) for row in rows] == [
        ("2024-03-01", "BRH4"),
        ("2024-03-01", "BRJ4"),
        ("2024-03-04", "BRH4"),
        ("2024-03-04", "BRJ4"),
    ]
    # Active-contract semantics stay separate in SESSIONS.
    assert snapshot.sheets["SESSIONS"][1:] == [
        ["BR", "BRH4", "2024-03-01"],
        ["BR", "BRJ4", "2024-03-04"],
    ]
    assert ["mirror_scope", "FULL_PERSISTENT_CONTRACT_D1"] in snapshot.sheets["SYNC_STATUS"]


def test_build_market_mirror_snapshot_fails_when_verified_session_has_no_candle() -> None:
    with pytest.raises(RuntimeError, match="parity failed"):
        build_market_mirror_snapshot(
            FakeSource(omit_second=True),
            symbol="BR",
            from_date="2024-03-01",
            till_date="2024-03-04",
        )
