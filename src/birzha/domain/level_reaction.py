"""Observed OHLC facts, never acceptance, rejection or directional control."""
from dataclasses import asdict, dataclass

REACTION_FACTS_VERSION = "LEVEL_REACTION_FACTS_V1"


@dataclass(frozen=True, slots=True)
class ReactionBar:
    begin: str
    end: str
    observed_at: str
    available_at: str
    open: float
    high: float
    low: float
    close: float
    range_contains_level: bool
    high_above_level: bool
    low_below_level: bool
    close_side: str
    previous_close_side: str | None
    close_side_change: str | None
    returned_to_initial_close_side: bool

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LevelReaction:
    level_name: str
    price: float | None
    source_status: str
    status: str
    reason: str | None
    bars: tuple[ReactionBar, ...]

    def to_dict(self):
        available = self.status in {"AVAILABLE", "AVAILABLE_APPROXIMATE"}
        return {
            "level_name": self.level_name, "price": self.price,
            "source_status": self.source_status, "status": self.status,
            "reason": self.reason, "bars": [bar.to_dict() for bar in self.bars],
            "range_contact_bars": sum(bar.range_contains_level for bar in self.bars) if available else None,
            "close_side_changes": sum(bar.close_side_change is not None for bar in self.bars) if available else None,
            "returns_to_initial_close_side": sum(bar.returned_to_initial_close_side for bar in self.bars) if available else None,
            "close_counts": {side: sum(bar.close_side == side for bar in self.bars) for side in ("ABOVE", "ON", "BELOW")} if available else None,
            "acceptance": "UNAVAILABLE", "rejection": "UNAVAILABLE",
            "failed_breakout": "UNAVAILABLE", "holding_power": "UNAVAILABLE",
            "level_strength": "UNKNOWN", "control": "UNKNOWN",
        }
