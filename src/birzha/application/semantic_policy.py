"""Canonical semantic policy for natural-language BIRZHA requests.

This module is the top-level planning layer used by ChatGPT/Codex-facing
interfaces. It turns a human request into deterministic data requirements
without forcing the caller to choose low-level MCP tools, storage tables or
timeframes.

Operational data policy:
- D1: permanent archive from 2021-01-01 through the latest safe completed day.
- H1: rolling last 90 calendar days.
- M15: rolling last 30 calendar days.
- Indicators are calculated separately for each timeframe and cover the same
  effective timeframe range as the underlying candles.
- Raw volume is part of every candle series. Derived indicators are calculated
  from stored raw data; they are not downloaded as independent market data.
- TradeStats and FUTOI remain separate raw flow sources and can be aggregated
  into the relevant timeframe analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


CORE_SYMBOLS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
D1_ARCHIVE_START = "2021-01-01"
ROLLING_WINDOW_DAYS = {"H1": 90, "M15": 30}
TIMEFRAMES = ("D1", "H1", "M15")

INDICATOR_FIELDS = (
    "return_5",
    "return_10",
    "return_20",
    "sma_20",
    "sma_50",
    "efficiency_ratio_20",
    "atr_14_pct",
    "volume_ratio_20",
    "vwap_20",
    "price_location_20",
    "support_20",
    "resistance_20",
    "trend_score",
)

INDICATOR_TABS = {
    "D1": "D1_INDICATORS",
    "H1": "H1_INDICATORS",
    "M15": "M15_INDICATORS",
}

FUTURES_ROOTS = frozenset({"SI", "BR", "GOLD"})
TRADESTATS_SYMBOLS = frozenset({"SBER", "SI", "BR", "GOLD"})

_SYMBOL_ALIASES = {
    "sber": "SBER",
    "сбер": "SBER",
    "сбербанк": "SBER",
    "si": "Si",
    "доллар-рубль": "Si",
    "доллар рубль": "Si",
    "br": "BR",
    "brent": "BR",
    "брент": "BR",
    "gold": "GOLD",
    "золото": "GOLD",
    "imoex": "IMOEX",
    "mix_mx": "IMOEX",
    "мосбиржи": "IMOEX",
    "индекс мосбиржи": "IMOEX",
    "rtsi": "RTSI",
    "rts": "RTSI",
    "ртс": "RTSI",
    "индекс ртс": "RTSI",
}

_BROAD_MARKERS = (
    "рынок",
    "весь рынок",
    "по рынку",
    "текущая ситуация",
    "общая ситуация",
    "все инструменты",
    "всех инструмент",
)
_FORECAST_MARKERS = ("прогноз", "forecast")
_ANALYSIS_MARKERS = ("аналитик", "анализ", "оцени", "ситуаци", "рынок")


@dataclass(frozen=True, slots=True)
class TimeframeRequirement:
    timeframe: str
    from_date: str
    till_date: str
    storage_mode: str
    indicator_tab: str
    indicator_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "timeframe": self.timeframe,
            "from_date": self.from_date,
            "till_date": self.till_date,
            "storage_mode": self.storage_mode,
            "indicator_tab": self.indicator_tab,
            "indicator_fields": list(self.indicator_fields),
        }


@dataclass(frozen=True, slots=True)
class SymbolRequirement:
    symbol: str
    timeframes: tuple[TimeframeRequirement, ...]
    raw_volume_required: bool
    tradestats_required: bool
    futoi_required: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframes": [item.to_dict() for item in self.timeframes],
            "raw_volume_required": self.raw_volume_required,
            "tradestats_required": self.tradestats_required,
            "futoi_required": self.futoi_required,
        }


@dataclass(frozen=True, slots=True)
class SemanticPlan:
    intent: str
    question: str
    as_of_date: str
    scope: str
    symbols: tuple[str, ...]
    requirements: tuple[SymbolRequirement, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "question": self.question,
            "as_of_date": self.as_of_date,
            "scope": self.scope,
            "symbols": list(self.symbols),
            "requirements": [item.to_dict() for item in self.requirements],
            "policy": {
                "D1": "permanent_from_2021-01-01",
                "H1": "rolling_90_calendar_days",
                "M15": "rolling_30_calendar_days",
                "reuse_existing_data": True,
                "fetch_only_missing_ranges": True,
                "indicators_match_timeframe_range": True,
                "raw_volume_is_candle_field": True,
            },
        }


def build_semantic_plan(question: str, *, as_of_date: str | None = None) -> SemanticPlan:
    text = " ".join(question.strip().lower().split())
    if not text:
        raise ValueError("question must be non-empty")

    till = date.fromisoformat(as_of_date[:10]) if as_of_date else date.today()
    detected = _detect_symbols(text)
    broad = any(marker in text for marker in _BROAD_MARKERS)

    if broad and not detected:
        symbols = CORE_SYMBOLS
        scope = "CORE_MARKET"
    elif detected:
        symbols = tuple(detected)
        scope = "SELECTED_SYMBOLS"
    else:
        raise ValueError(
            "instrument is not resolved; name a market instrument or ask for a broad market analysis"
        )

    if any(marker in text for marker in _FORECAST_MARKERS):
        intent = "FORECAST"
    elif any(marker in text for marker in _ANALYSIS_MARKERS):
        intent = "ANALYSIS"
    else:
        intent = "MARKET_QUERY"

    requirements = tuple(_symbol_requirement(symbol, till) for symbol in symbols)
    return SemanticPlan(
        intent=intent,
        question=question,
        as_of_date=till.isoformat(),
        scope=scope,
        symbols=symbols,
        requirements=requirements,
    )


def _detect_symbols(text: str) -> list[str]:
    found: list[str] = []
    for alias, symbol in _SYMBOL_ALIASES.items():
        if alias in text and symbol not in found:
            found.append(symbol)
    return found


def _symbol_requirement(symbol: str, till: date) -> SymbolRequirement:
    upper = symbol.upper()
    timeframes = (
        TimeframeRequirement(
            timeframe="D1",
            from_date=D1_ARCHIVE_START,
            till_date=till.isoformat(),
            storage_mode="PERMANENT",
            indicator_tab=INDICATOR_TABS["D1"],
            indicator_fields=INDICATOR_FIELDS,
        ),
        TimeframeRequirement(
            timeframe="H1",
            from_date=(till - timedelta(days=ROLLING_WINDOW_DAYS["H1"])).isoformat(),
            till_date=till.isoformat(),
            storage_mode="ROLLING_90D",
            indicator_tab=INDICATOR_TABS["H1"],
            indicator_fields=INDICATOR_FIELDS,
        ),
        TimeframeRequirement(
            timeframe="M15",
            from_date=(till - timedelta(days=ROLLING_WINDOW_DAYS["M15"])).isoformat(),
            till_date=till.isoformat(),
            storage_mode="ROLLING_30D",
            indicator_tab=INDICATOR_TABS["M15"],
            indicator_fields=INDICATOR_FIELDS,
        ),
    )
    return SymbolRequirement(
        symbol=symbol,
        timeframes=timeframes,
        raw_volume_required=True,
        tradestats_required=upper in TRADESTATS_SYMBOLS,
        futoi_required=upper in FUTURES_ROOTS,
    )
