from __future__ import annotations

from birzha.application.historical_data import _verification_symbol
from birzha.application.market_mirror import build_market_mirror_snapshot
from birzha.domain.market import Candle, CandleSeries, Instrument


def test_market_mirror_preserves_si_case_for_durable_verification_key():
    expected_key = _verification_symbol("Si", "D1", is_root=True)

    instrument = Instrument(
        symbol="Si",
        secid="SiZ6",
        board="RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        root_symbol="Si",
    )
    series = CandleSeries(
        instrument=instrument,
        timeframe="D1",
        candles=(
            Candle(
                open=83000.0,
                high=83500.0,
                low=82800.0,
                close=83300.0,
                value=1.0,
                volume=2.0,
                begin="2026-09-15T00:00:00+03:00",
                end="2026-09-15T23:59:59+03:00",
                completed=True,
            ),
        ),
    )

    class Source:
        verification_queries: list[str] = []
        session_queries: list[str] = []

        def is_session_range_verified(self, symbol, from_date, till_date):
            self.verification_queries.append(symbol)
            return symbol == expected_key

        def stored_session_contracts(self, symbol, from_date, till_date):
            self.session_queries.append(symbol)
            return (("2026-09-15", "SiZ6"),) if symbol == expected_key else ()

        def stored_instrument(self, secid):
            return instrument if secid == "SiZ6" else None

        def read(self, instrument_arg, timeframe, from_date, till_date):
            assert instrument_arg == instrument
            assert timeframe == "D1"
            return series

    source = Source()
    snapshot = build_market_mirror_snapshot(
        source,
        symbol="Si",
        from_date="2026-09-15",
        till_date="2026-09-15",
    )

    assert source.verification_queries == [expected_key]
    assert source.session_queries == [expected_key]
    assert expected_key.startswith("Si#")
    assert snapshot.symbol == "SI"
    assert snapshot.data_rows == 1
    assert snapshot.contract_count == 1
