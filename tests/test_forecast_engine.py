from birzha.application.forecast import build_forecast_from_snapshot
from birzha.domain.forecast import FORECAST_RECORD_CONTRACT_VERSION\nfrom birzha.domain.snapshot import MarketSnapshot, TimeframeState


def _state(tf: str, trend: float, ret5: float, atr: float | None = None) -> TimeframeState:
    return TimeframeState(
        timeframe=tf,
        candles=100,
        last_close=100.0,
        return_5=ret5,
        return_10=ret5 * 1.5,
        return_20=ret5 * 2.0,
        sma_20=98.0,
        sma_50=95.0,
        efficiency_ratio_20=0.5,
        atr_14_pct=atr,
        volume_ratio_20=1.1,
        trend_score=trend,
    )


def test_forecast_up_when_timeframes_align():
    snapshot = MarketSnapshot(
        symbol="Si",
        secid="SiU6",
        as_of="2026-08-28T18:49:59",
        source="MOEX_ISS",
        d1=_state("D1", 3.0, 0.02, 0.01),
        h1=_state("H1", 2.5, 0.01),
        m15=_state("M15", 2.0, 0.005),
        data_quality="PASS",
        warnings=(),
    )

    forecast = build_forecast_from_snapshot(snapshot)

    assert forecast.direction == "UP"
    assert forecast.control == "BUYERS"
    assert forecast.route == "TREND"
    assert [h.sessions for h in forecast.horizons] == [5, 10, 20]
    assert all(h.expected_move_pct is not None and h.expected_move_pct > 0 for h in forecast.horizons)
    assert forecast.validation_status == "UNVALIDATED_BASELINE"
    assert forecast.forecast_id.startswith("fcst_")
    assert forecast.record_version == FORECAST_RECORD_CONTRACT_VERSION
    assert forecast.snapshot_id is not None
    assert forecast.snapshot_id.startswith("snap_")
    assert forecast.snapshot_contract_version == "MARKET_SNAPSHOT_V2"
    assert forecast.prediction_contract_id is not None
    assert forecast.prediction_contract_id.startswith("pred_")
    payload = forecast.to_dict()
    assert payload["as_of"] == snapshot.as_of
    assert payload["instrument"] == {"symbol": "Si", "contract_id": "SiU6"}
    assert payload["field_availability"]["market_state"] == "UNAVAILABLE"
    assert payload["field_availability"]["entries"] == "UNAVAILABLE"
    assert payload["versions"]["forecast_record"] == FORECAST_RECORD_CONTRACT_VERSION


def test_forecast_identity_is_deterministic():
    snapshot = MarketSnapshot(
        symbol="Si",
        secid="SiU6",
        as_of="2026-08-28T18:49:59",
        source="MOEX_ISS",
        d1=_state("D1", 0.0, 0.0, 0.01),
        h1=_state("H1", 0.0, 0.0),
        m15=_state("M15", 0.0, 0.0),
        data_quality="PASS",
        warnings=(),
    )
    assert build_forecast_from_snapshot(snapshot).forecast_id == build_forecast_from_snapshot(snapshot).forecast_id
