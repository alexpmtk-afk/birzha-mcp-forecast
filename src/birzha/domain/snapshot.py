"""Causal Market Snapshot v2 domain contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from birzha.domain.flow import MarketFlowSnapshot
from birzha.domain.volume_profile import VolumeProfileResult


MARKET_SNAPSHOT_CONTRACT_VERSION = "MARKET_SNAPSHOT_V2"


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
    vwap_20: float | None = None
    price_location_20: float | None = None
    support_20: float | None = None
    resistance_20: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TimeframeQuality:
    timeframe: str
    candles: int
    minimum_required: int
    latest_completed_end: str | None
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DataQualityContract:
    version: str
    status: str
    timeframes: tuple[TimeframeQuality, ...]
    flow_status: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "status": self.status,
            "timeframes": [item.to_dict() for item in self.timeframes],
            "flow_status": self.flow_status,
            "reasons": list(self.reasons),
        }


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
    quality_contract: DataQualityContract | None = None
    volume_profile: VolumeProfileResult | None = None
    contract_version: str = MARKET_SNAPSHOT_CONTRACT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "symbol": self.symbol,
            "secid": self.secid,
            "as_of": self.as_of,
            "source": self.source,
            "d1": self.d1.to_dict(),
            "h1": self.h1.to_dict(),
            "m15": self.m15.to_dict(),
            "flow": self.flow.to_dict() if self.flow else None,
            "volume_profile": self.volume_profile.to_dict() if self.volume_profile else None,
            "data_quality": self.data_quality,
            "quality_contract": self.quality_contract.to_dict() if self.quality_contract else None,
            "warnings": list(self.warnings),
        }
