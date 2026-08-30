"""Universal market-domain contracts.

The domain deliberately has no Si/SBER-specific conditionals. Provider-specific
routing lives outside these types.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


AssetClass = Literal["future", "equity", "index", "fx", "commodity", "unknown"]


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    secid: str
    board: str
    engine: str
    market: str
    asset_class: AssetClass
    name: str | None = None
    root_symbol: str | None = None
    last_trade_date: str | None = None
    source: str = "MOEX_ISS"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Candle:
    open: float | None
    close: float | None
    high: float | None
    low: float | None
    value: float | None
    volume: float | None
    begin: str
    end: str
    completed: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CandleSeries:
    instrument: Instrument
    timeframe: str
    candles: tuple[Candle, ...]
    source: str = "MOEX_ISS"

    @property
    def count(self) -> int:
        return len(self.candles)

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument.to_dict(),
            "timeframe": self.timeframe,
            "count": self.count,
            "source": self.source,
            "candles": [c.to_dict() for c in self.candles],
        }
