from birzha.domain.market import Candle, CandleSeries, Instrument


def test_candle_series_contract():
    instrument = Instrument(
        symbol="Si",
        secid="SiU6",
        board="RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        root_symbol="Si",
    )
    series = CandleSeries(
        instrument=instrument,
        timeframe="H1",
        candles=(
            Candle(
                open=100.0,
                close=101.0,
                high=102.0,
                low=99.0,
                value=1000.0,
                volume=10.0,
                begin="2026-08-28T10:00:00",
                end="2026-08-28T10:59:59",
            ),
        ),
    )
    payload = series.to_dict()
    assert payload["count"] == 1
    assert payload["instrument"]["secid"] == "SiU6"
    assert payload["candles"][0]["close"] == 101.0
