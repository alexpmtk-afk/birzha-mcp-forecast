"""Volume Profile domain contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class PriceVolumePoint:
    price: float
    volume: float


@dataclass(frozen=True, slots=True)
class VolumeBin:
    low: float
    high: float
    center: float
    volume: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VolumeProfileResult:
    poc: float
    vah: float
    val: float
    value_area_fraction: float
    hvn: tuple[float, ...]
    lvn: tuple[float, ...]
    shape: str
    bins: tuple[VolumeBin, ...]
    total_volume: float
    method: str = "PRICE_VOLUME_POINTS_V1"

    def to_dict(self) -> dict[str, object]:
        return {
            "poc": self.poc,
            "vah": self.vah,
            "val": self.val,
            "value_area_fraction": self.value_area_fraction,
            "hvn": list(self.hvn),
            "lvn": list(self.lvn),
            "shape": self.shape,
            "total_volume": self.total_volume,
            "method": self.method,
            "bins": [item.to_dict() for item in self.bins],
        }
