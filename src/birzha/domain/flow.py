"""Provider-neutral order-flow and open-interest contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ClientOpenInterest:
    client_group: str
    net_position: float | None
    long_position: float | None
    short_position: float | None
    long_accounts: int | None
    short_accounts: int | None
    observed_at: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MarketFlowSnapshot:
    symbol: str
    secid: str
    from_date: str
    till_date: str
    as_of: str | None
    source: str
    intervals: int
    buy_volume: float | None
    sell_volume: float | None
    volume_delta: float | None
    volume_delta_ratio: float | None
    buy_value: float | None
    sell_value: float | None
    value_delta: float | None
    price_change_pct: float | None
    algopack_oi_open: float | None
    algopack_oi_close: float | None
    algopack_oi_change: float | None
    individuals: ClientOpenInterest | None
    legal_entities: ClientOpenInterest | None
    data_quality: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "secid": self.secid,
            "from_date": self.from_date,
            "till_date": self.till_date,
            "as_of": self.as_of,
            "source": self.source,
            "intervals": self.intervals,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "volume_delta": self.volume_delta,
            "volume_delta_ratio": self.volume_delta_ratio,
            "buy_value": self.buy_value,
            "sell_value": self.sell_value,
            "value_delta": self.value_delta,
            "price_change_pct": self.price_change_pct,
            "algopack_oi_open": self.algopack_oi_open,
            "algopack_oi_close": self.algopack_oi_close,
            "algopack_oi_change": self.algopack_oi_change,
            "individuals": self.individuals.to_dict() if self.individuals else None,
            "legal_entities": self.legal_entities.to_dict() if self.legal_entities else None,
            "data_quality": self.data_quality,
            "warnings": list(self.warnings),
        }
