from __future__ import annotations

from dataclasses import replace

from birzha.application.market_state import build_market_state_vector
from birzha.domain.flow import MarketFlowSnapshot
from birzha.domain.market import Instrument
from birzha.domain.normalized_features import NormalizedFeatureSet
from birzha.domain.snapshot import (
    DataQualityContract,
    MarketSnapshot,
    TimeframeQuality,
    TimeframeState,
    market_snapshot_id,
)


def _state(timeframe: str, candles: int) -> TimeframeState:
    return TimeframeState(
        timeframe=timeframe,
        candles=candles,
        last_close=100.0,
        return_5=0.02,
        return_10=0.03,
        return_20=0.04,
        sma_20=99.0,
        sma_50=98.0,
        efficiency_ratio_20=0.4,
        atr_14_pct=0.02,
        volume_ratio_20=1.2,
        trend_score=1.0,
        vwap_20=99.5,
        price_location_20=0.7,
    )


def _snapshot(*, d1_candles: int = 60, h1_candles: int = 60) -> MarketSnapshot:
    counts = {"D1": d1_candles, "H1": h1_candles, "M15": 60}
    states = {tf: _state(tf, count) for tf, count in counts.items()}
    quality = tuple(
        TimeframeQuality(
            timeframe=tf,
            candles=count,
            minimum_required=50,
            latest_completed_end="2026-10-01T18:00:00+03:00",
            status="PASS" if count >= 50 else "DEGRADED",
        )
        for tf, count in counts.items()
    )
    features = NormalizedFeatureSet(
        current_price=100.0,
        d1_atr_price_scale=2.0,
        d1_return_5_atr=0.5,
        d1_return_20_atr=1.0,
        h1_return_5_atr=0.4,
        m15_return_5_atr=0.3,
        d1_efficiency_20=0.4,
        h1_efficiency_20=0.3,
        m15_efficiency_20=0.2,
        d1_price_location_20=0.7,
        h1_price_location_20=0.6,
        m15_price_location_20=0.5,
        d1_volume_ratio_20=1.2,
        h1_volume_ratio_20=1.1,
        m15_volume_ratio_20=1.0,
        distance_to_session_vwap_atr=0.25,
        distance_to_poc_atr=0.1,
        distance_to_vah_atr=-0.2,
        distance_to_val_atr=0.6,
        delta_volume_ratio=0.15,
        oi_change_ratio=None,
        profile_method="CANDLE_VOLUME_PROXY_V1",
        profile_is_exact=False,
    )
    return MarketSnapshot(
        symbol="TEST",
        secid="TEST1",
        as_of="2026-10-01T18:00:00+03:00",
        source="TEST",
        d1=states["D1"],
        h1=states["H1"],
        m15=states["M15"],
        data_quality="PASS" if d1_candles >= 50 else "DEGRADED",
        warnings=("VOLUME_PROFILE:approximate_candle_proxy",),
        quality_contract=DataQualityContract(
            version="TEST",
            status="PASS" if d1_candles >= 50 else "DEGRADED",
            timeframes=quality,
            flow_status="NOT_REQUESTED",
            reasons=(),
        ),
        normalized_features=features,
    )


def _instrument(asset_class: str, *capabilities: str) -> Instrument:
    return Instrument(
        symbol="TEST",
        secid="TEST1",
        board="TQBR",
        engine="stock",
        market="shares",
        asset_class=asset_class,  # type: ignore[arg-type]
        data_capabilities=tuple(capabilities),
    )


def _availability(vector: dict[str, object], name: str) -> dict[str, object]:
    return vector["availability"][name]  # type: ignore[index,return-value]


def _flow(
    data_quality: str,
    warnings: tuple[str, ...] = (),
    *,
    session_vwap_source: str = "PUBLIC_TRADES_PRICE_QUANTITY",
) -> MarketFlowSnapshot:
    return MarketFlowSnapshot(
        symbol="TEST",
        secid="TEST1",
        from_date="2026-09-27",
        till_date="2026-10-01",
        as_of="2026-10-01T18:00:00+03:00",
        source="TEST",
        intervals=1,
        buy_volume=100.0,
        sell_volume=90.0,
        volume_delta=10.0,
        volume_delta_ratio=0.15,
        buy_value=1000.0,
        sell_value=900.0,
        value_delta=100.0,
        price_change_pct=0.1,
        algopack_oi_open=1000.0,
        algopack_oi_close=1010.0,
        algopack_oi_change=10.0,
        individuals=None,
        legal_entities=None,
        data_quality=data_quality,
        warnings=warnings,
        session_vwap=99.5,
        session_vwap_source=session_vwap_source,
    )


def _with_flow(snapshot: MarketSnapshot, flow: MarketFlowSnapshot) -> MarketSnapshot:
    return replace(snapshot, flow=flow)


def test_market_state_vector_marks_unsupported_index_features_not_applicable() -> None:
    snapshot = _snapshot()
    vector = build_market_state_vector(
        snapshot,
        _instrument("index", "CANDLES", "TRADING_CALENDAR"),
    ).to_dict()

    assert vector["classification_status"] == "NOT_CLASSIFIED"
    assert vector["snapshot_id"] == market_snapshot_id(snapshot)
    assert _availability(vector, "d1_volume_ratio_20")["status"] == "NOT_APPLICABLE"
    assert _availability(vector, "delta_volume_ratio")["status"] == "NOT_APPLICABLE"
    assert _availability(vector, "oi_change_ratio")["status"] == "NOT_APPLICABLE"
    assert _availability(vector, "distance_to_poc_atr")["status"] == "NOT_APPLICABLE"
    assert vector["features"]["d1_volume_ratio_20"] is None
    assert vector["features"]["distance_to_poc_atr"] is None
    assert vector["warnings"] == []


def test_market_state_vector_keeps_approximate_profile_explicit() -> None:
    vector = build_market_state_vector(
        _with_flow(_snapshot(), _flow("PASS")),
        _instrument("equity", "CANDLES", "VOLUME", "TRADESTATS"),
    ).to_dict()

    assert _availability(vector, "delta_volume_ratio")["status"] == "AVAILABLE"
    assert _availability(vector, "oi_change_ratio")["status"] == "NOT_APPLICABLE"
    assert _availability(vector, "distance_to_poc_atr") == {
        "status": "AVAILABLE_APPROXIMATE",
        "reason": "candle_volume_proxy_profile",
    }
    assert _availability(vector, "profile_is_exact")["status"] == "AVAILABLE"
    assert vector["features"]["profile_is_exact"] is False


def test_flow_pass_keeps_flow_derived_evidence_available() -> None:
    vector = build_market_state_vector(
        _with_flow(_snapshot(), _flow("PASS")),
        _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "OPEN_INTEREST"),
    ).to_dict()

    assert _availability(vector, "delta_volume_ratio")["status"] == "AVAILABLE"
    assert _availability(vector, "distance_to_session_vwap_atr")["status"] == "AVAILABLE"


def test_tradestats_degradation_masks_values_from_that_source() -> None:
    flow = _flow("DEGRADED", ("ALGOPACK_TRADESTATS_UNAVAILABLE:TimeoutError",),
                 session_vwap_source="TRADESTATS_VWAP_WEIGHTED")
    vector = build_market_state_vector(
        _with_flow(_snapshot(), flow),
        _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "OPEN_INTEREST"),
    ).to_dict()

    assert _availability(vector, "delta_volume_ratio") == {
        "status": "UNAVAILABLE",
        "reason": "flow_tradestats_degraded",
    }
    assert vector["features"]["delta_volume_ratio"] is None
    assert _availability(vector, "distance_to_session_vwap_atr")["status"] == "UNAVAILABLE"
    assert _availability(vector, "oi_change_ratio")["status"] == "UNAVAILABLE"


def test_degraded_flow_with_missing_tradestats_delta_stays_unavailable() -> None:
    snapshot = _snapshot()
    assert snapshot.normalized_features is not None
    flow = replace(
        _flow("DEGRADED", ("ALGOPACK_TRADESTATS_EMPTY",)),
        volume_delta_ratio=None,
    )
    incomplete = replace(
        snapshot,
        flow=flow,
        normalized_features=replace(
            snapshot.normalized_features,
            delta_volume_ratio=None,
        ),
    )

    vector = build_market_state_vector(
        incomplete,
        _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "OPEN_INTEREST"),
    ).to_dict()

    assert _availability(vector, "delta_volume_ratio")["status"] == "UNAVAILABLE"
    assert vector["features"]["delta_volume_ratio"] is None


def test_futoi_only_degradation_does_not_mask_independent_delta_evidence() -> None:
    vector = build_market_state_vector(
        _with_flow(_snapshot(), _flow("DEGRADED", ("FUTOI_EMPTY",))),
        _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "OPEN_INTEREST"),
    ).to_dict()

    assert _availability(vector, "delta_volume_ratio")["status"] == "AVAILABLE"
    assert vector["features"]["delta_volume_ratio"] == 0.15
    assert _availability(vector, "distance_to_session_vwap_atr")["status"] == "AVAILABLE"


def test_approximate_profile_requires_minimum_h1_history() -> None:
    vector = build_market_state_vector(
        _snapshot(h1_candles=49),
        _instrument("equity", "CANDLES", "VOLUME", "TRADESTATS"),
    ).to_dict()

    assert _availability(vector, "distance_to_poc_atr") == {
        "status": "INSUFFICIENT_HISTORY",
        "reason": "H1_requires_50_candles",
    }
    assert vector["features"]["distance_to_poc_atr"] is None
    assert _availability(vector, "profile_is_exact")["status"] == "AVAILABLE"


def test_missing_applicable_open_interest_is_unavailable_not_zero() -> None:
    vector = build_market_state_vector(
        _snapshot(),
        _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "OPEN_INTEREST", "FUTOI"),
    ).to_dict()

    assert _availability(vector, "oi_change_ratio")["status"] == "UNAVAILABLE"
    assert vector["features"]["oi_change_ratio"] is None


def test_short_timeframe_history_suppresses_features_with_explicit_status() -> None:
    vector = build_market_state_vector(
        _snapshot(d1_candles=49),
        _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "OPEN_INTEREST"),
    ).to_dict()

    assert _availability(vector, "d1_return_5_atr") == {
        "status": "INSUFFICIENT_HISTORY",
        "reason": "D1_requires_50_candles",
    }
    assert vector["features"]["d1_return_5_atr"] is None
    assert _availability(vector, "h1_return_5_atr")["status"] == "AVAILABLE"


def test_market_state_service_reuses_the_snapshot_instrument_resolution() -> None:
    from birzha.application.market_state import MarketStateService

    snapshot = _snapshot()
    instrument = _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "FUTOI")

    class SnapshotStub:
        calls = 0

        def build_with_instrument(self, symbol: str, *, as_of_date: str | None = None):
            self.calls += 1
            assert symbol == "TEST"
            assert as_of_date == "2026-10-01"
            return snapshot, instrument

    snapshots = SnapshotStub()
    result = MarketStateService(snapshots).build("TEST", as_of_date="2026-10-01")

    assert snapshots.calls == 1
    assert result["secid"] == "TEST1"
    assert result["snapshot_id"] == market_snapshot_id(snapshot)


def test_candle_features_are_not_applicable_without_candle_capability() -> None:
    vector = build_market_state_vector(
        _snapshot(),
        _instrument("unknown", "TRADING_CALENDAR"),
    ).to_dict()

    assert _availability(vector, "current_price")["status"] == "NOT_APPLICABLE"
    assert vector["features"]["current_price"] is None


def test_applicable_but_missing_delta_and_profile_remain_unavailable() -> None:
    from dataclasses import replace

    snapshot = _snapshot()
    assert snapshot.normalized_features is not None
    incomplete = replace(
        snapshot,
        normalized_features=replace(
            snapshot.normalized_features,
            delta_volume_ratio=None,
            distance_to_poc_atr=None,
            distance_to_vah_atr=None,
            distance_to_val_atr=None,
            profile_method=None,
            profile_is_exact=None,
        ),
    )
    vector = build_market_state_vector(
        incomplete,
        _instrument("future", "CANDLES", "VOLUME", "TRADESTATS", "FUTOI"),
    ).to_dict()

    assert _availability(vector, "delta_volume_ratio")["status"] == "UNAVAILABLE"
    assert _availability(vector, "distance_to_poc_atr")["status"] == "UNAVAILABLE"
    assert vector["features"]["delta_volume_ratio"] is None
    assert vector["features"]["distance_to_poc_atr"] is None
