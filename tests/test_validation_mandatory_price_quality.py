from birzha.application.validation import _mandatory_price_quality_pass
from birzha.domain.snapshot import (
    DataQualityContract,
    MarketSnapshot,
    TimeframeQuality,
    TimeframeState,
)


def _state(timeframe: str, candles: int = 60) -> TimeframeState:
    return TimeframeState(
        timeframe=timeframe,
        candles=candles,
        last_close=100.0,
        return_5=0.01,
        return_10=0.02,
        return_20=0.03,
        sma_20=99.0,
        sma_50=98.0,
        efficiency_ratio_20=0.4,
        atr_14_pct=0.02,
        volume_ratio_20=1.0,
        trend_score=1.0,
    )


def _snapshot(*, d1_status: str = "PASS", flow_status: str = "DEGRADED") -> MarketSnapshot:
    qualities = (
        TimeframeQuality("D1", 60 if d1_status == "PASS" else 20, 50, "2026-01-01", d1_status),
        TimeframeQuality("H1", 60, 50, "2026-01-01", "PASS"),
        TimeframeQuality("M15", 60, 50, "2026-01-01", "PASS"),
    )
    quality = DataQualityContract(
        version="TEST",
        status="DEGRADED" if flow_status != "PASS" or d1_status != "PASS" else "PASS",
        timeframes=qualities,
        flow_status=flow_status,
        reasons=("optional flow missing",) if flow_status != "PASS" else (),
    )
    return MarketSnapshot(
        symbol="IMOEX",
        secid="IMOEX",
        as_of="2026-01-01T18:00:00+03:00",
        source="TEST",
        d1=_state("D1", qualities[0].candles),
        h1=_state("H1"),
        m15=_state("M15"),
        data_quality=quality.status,
        warnings=quality.reasons,
        quality_contract=quality,
    )


def test_optional_flow_degradation_does_not_reject_complete_price_snapshot() -> None:
    assert _mandatory_price_quality_pass(
        _snapshot(d1_status="PASS", flow_status="DEGRADED")
    ) is True


def test_incomplete_mandatory_price_timeframe_is_rejected() -> None:
    assert _mandatory_price_quality_pass(
        _snapshot(d1_status="DEGRADED", flow_status="PASS")
    ) is False
