"""Mathematical Volume Profile engine from exact price-volume points."""

from __future__ import annotations

from dataclasses import dataclass

from birzha.domain.volume_profile import PriceVolumePoint, VolumeBin, VolumeProfileResult


@dataclass(frozen=True, slots=True)
class VolumeProfileEngine:
    bins: int = 24
    value_area_fraction: float = 0.70
    method: str = "PRICE_VOLUME_POINTS_V1"

    def build(self, points: list[PriceVolumePoint] | tuple[PriceVolumePoint, ...]) -> VolumeProfileResult:
        usable = tuple(p for p in points if p.volume > 0)
        if not usable:
            raise ValueError("volume profile requires positive price-volume points")
        lo = min(p.price for p in usable)
        hi = max(p.price for p in usable)
        if hi <= lo:
            hi = lo + 1e-9
        count = max(4, int(self.bins))
        width = (hi - lo) / count
        volumes = [0.0] * count
        for point in usable:
            index = min(count - 1, max(0, int((point.price - lo) / width)))
            volumes[index] += point.volume
        bins = tuple(
            VolumeBin(
                low=lo + i * width,
                high=lo + (i + 1) * width,
                center=lo + (i + 0.5) * width,
                volume=volumes[i],
            )
            for i in range(count)
        )
        total = sum(volumes)
        poc_index = max(range(count), key=lambda i: volumes[i])
        selected = {poc_index}
        accumulated = volumes[poc_index]
        left = poc_index - 1
        right = poc_index + 1
        target = total * self.value_area_fraction
        while accumulated < target and (left >= 0 or right < count):
            left_volume = volumes[left] if left >= 0 else -1.0
            right_volume = volumes[right] if right < count else -1.0
            if right_volume > left_volume:
                selected.add(right); accumulated += right_volume; right += 1
            else:
                selected.add(left); accumulated += left_volume; left -= 1
        nonzero = [v for v in volumes if v > 0]
        average = sum(nonzero) / len(nonzero)
        hvn = tuple(bins[i].center for i, v in enumerate(volumes) if v >= average * 1.35)
        lvn = tuple(bins[i].center for i, v in enumerate(volumes) if 0 < v <= average * 0.55)
        shape = _classify_shape(volumes)
        return VolumeProfileResult(
            poc=bins[poc_index].center,
            vah=max(bins[i].high for i in selected),
            val=min(bins[i].low for i in selected),
            value_area_fraction=self.value_area_fraction,
            hvn=hvn,
            lvn=lvn,
            shape=shape,
            bins=bins,
            total_volume=total,
            method=self.method,
        )


def _classify_shape(volumes: list[float]) -> str:
    total = sum(volumes)
    if total <= 0:
        return "UNKNOWN"
    n = len(volumes)
    lower = sum(volumes[: n // 3]) / total
    upper = sum(volumes[-(n // 3) :]) / total
    middle = sum(volumes[n // 3 : n - n // 3]) / total
    peaks = _peak_count(volumes)
    if peaks >= 2 and middle < max(lower, upper):
        return "B"
    if upper >= 0.50 and lower <= 0.22:
        return "P"
    if lower >= 0.50 and upper <= 0.22:
        return "b"
    return "D"


def _peak_count(volumes: list[float]) -> int:
    if len(volumes) < 3:
        return 1 if volumes else 0
    average = sum(volumes) / len(volumes)
    threshold = average * 1.20
    peaks = 0
    for i in range(1, len(volumes) - 1):
        if volumes[i] >= threshold and volumes[i] >= volumes[i - 1] and volumes[i] >= volumes[i + 1]:
            peaks += 1
    return peaks


def profile_from_candles(series, *, bins: int = 24):
    points=[]
    for c in series.candles:
        if c.volume is None or c.volume <= 0 or c.close is None:
            continue
        if c.high is not None and c.low is not None:
            price=(c.high+c.low+c.close)/3.0
        else:
            price=c.close
        points.append(PriceVolumePoint(price=float(price), volume=float(c.volume)))
    if not points:
        return None
    return VolumeProfileEngine(bins=bins, method="CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1").build(points)
