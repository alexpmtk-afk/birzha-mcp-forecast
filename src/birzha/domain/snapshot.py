"""Causal Market Snapshot v2 draft domain contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from birzha.domain.flow import MarketFlowSnapshot


@dataclass(frozen=True, slots=True)
class TimeframeState:
    timeframe: str
    candles: int
    last_close: float | None
    return_5: float | None
    return_10: float | None
    return_20: float | None
    sma_20: float | None
    sma_50: float | None
    efficiency_ratio_20: float | None
    atr_14_pct: float | None
    volume_ratio_20: float | None
    trend_score: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    secid: str
    as_of: str
    source: str
    d1: TimeframeState
    h1: TimeframeState
    m15: TimeframeState
    data_quality: str
    warnings: tuple[str, ...]
    flow: MarketFlowSnapshot | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "secid": self.secid,
            "as_of": self.as_of,
            "source": self.source,
            "d1": self.d1.to_dict(),
            "h1": self.h1.to_dict(),
            "m15": self.m15.to_dict(),
            "flow": self.flow.to_dict() if self.flow else None,
            "data_quality": self.data_quality,
            "warnings": list(self.warnings),
        }
