"""Versioned evidence-vector contract for the first Forecast Engine layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from birzha.domain.normalized_features import NormalizedFeatureSet

MARKET_STATE_VECTOR_VERSION = "MARKET_STATE_VECTOR_V0"
FeatureStatus = Literal[
    "AVAILABLE",
    "AVAILABLE_APPROXIMATE",
    "NOT_APPLICABLE",
    "UNAVAILABLE",
    "INSUFFICIENT_HISTORY",
]


@dataclass(frozen=True, slots=True)
class FeatureAvailability:
    status: FeatureStatus
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarketStateVector:
    """Causal inputs for future state logic; this contract classifies nothing."""

    symbol: str
    secid: str
    as_of: str
    snapshot_id: str
    snapshot_contract_version: str
    asset_class: str
    capabilities: tuple[str, ...]
    data_quality: str
    classification_status: str
    features: NormalizedFeatureSet
    availability: tuple[tuple[str, FeatureAvailability], ...]
    timeframe_evidence: tuple[tuple[str, int, int, str], ...]
    warnings: tuple[str, ...] = ()
    version: str = MARKET_STATE_VECTOR_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.version,
            "symbol": self.symbol,
            "secid": self.secid,
            "as_of": self.as_of,
            "snapshot_id": self.snapshot_id,
            "snapshot_contract_version": self.snapshot_contract_version,
            "asset_class": self.asset_class,
            "capabilities": list(self.capabilities),
            "data_quality": self.data_quality,
            "classification_status": self.classification_status,
            "features": self.features.to_dict(),
            "availability": {
                name: item.to_dict() for name, item in self.availability
            },
            "timeframe_evidence": {
                timeframe: {
                    "candles": candles,
                    "minimum_required": minimum,
                    "status": status,
                }
                for timeframe, candles, minimum, status in self.timeframe_evidence
            },
            "warnings": list(self.warnings),
        }
