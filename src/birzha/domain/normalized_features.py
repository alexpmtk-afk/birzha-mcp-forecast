"""Dimensionless normalized features for Protocol 08 Market State."""

from __future__ import annotations

from dataclasses import asdict, dataclass


NORMALIZED_FEATURES_FULL_WINDOWS_VERSION = "NORMALIZED_FEATURES_V2_FULL_WINDOWS_SMA_TR14"

NORMALIZED_FEATURES_VERSION = "NORMALIZED_FEATURES_V1"


@dataclass(frozen=True, slots=True)
class NormalizedFeatureSet:
    version: str = NORMALIZED_FEATURES_VERSION
    current_price: float | None = None
    d1_atr_price_scale: float | None = None

    d1_return_5_atr: float | None = None
    d1_return_20_atr: float | None = None
    h1_return_5_atr: float | None = None
    m15_return_5_atr: float | None = None

    d1_efficiency_20: float | None = None
    h1_efficiency_20: float | None = None
    m15_efficiency_20: float | None = None

    d1_price_location_20: float | None = None
    h1_price_location_20: float | None = None
    m15_price_location_20: float | None = None

    d1_volume_ratio_20: float | None = None
    h1_volume_ratio_20: float | None = None
    m15_volume_ratio_20: float | None = None

    distance_to_session_vwap_atr: float | None = None
    distance_to_poc_atr: float | None = None
    distance_to_vah_atr: float | None = None
    distance_to_val_atr: float | None = None

    delta_volume_ratio: float | None = None
    oi_change_ratio: float | None = None

    profile_method: str | None = None
    profile_is_exact: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
