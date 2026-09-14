"""Build deterministic human-auditable market-data mirror snapshots from YDB history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from birzha.application.historical_data import _verification_symbol
from birzha.application.market_data import is_futures_root_symbol
from birzha.domain.market import CandleSeries, Instrument


D1_COLUMNS = [
    "record_key", "calendar_date", "trade_session_date", "session_id",
    "bar_start_time", "bar_end_time", "available_at", "available_at_confidence",
    "contract_code", "open", "high", "low", "close", "volume",
    "number_of_trades", "turnover", "is_complete", "source_interval",
    "source_candles", "source_id",
]
SESSION_COLUMNS = ["symbol", "secid", "trade_date"]
VERIFIED_COLUMNS = ["scope", "symbol", "timeframe", "from_date", "till_date"]
SYNC_COLUMNS = ["key", "value"]


class MarketMirrorDataSource(Protocol):
    def stored_session_contracts(
        self, symbol: str, from_date: str, till_date: str
    ) -> tuple[tuple[str, str], ...]: ...

    def stored_instrument(self, secid: str) -> Instrument | None: ...

    def read(
        self, instrument: Instrument, timeframe: str, from_date: str, till_date: str
    ) -> CandleSeries: ...

    def is_session_range_verified(self, symbol: str, from_date: str, till_date: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class MarketMirrorSnapshot:
    symbol: str
    from_date: str
    till_date: str
    data_rows: int
    contract_count: int
    sheets: dict[str, list[list[object]]]


def _session_symbol(symbol: str) -> str:
    return _verification_symbol(
        symbol,
        "D1",
        is_root=is_futures_root_symbol(symbol),
    )


def build_market_mirror_snapshot(
    source: MarketMirrorDataSource,
    *,
    symbol: str,
    from_date: str,
    till_date: str,
) -> MarketMirrorSnapshot:
    """Build a fail-closed full D1 mirror from already verified persistent history.

    SESSIONS stores the causal active-contract map. D1 contains all persistent
    D1 rows for every contract participating in the verified root range,
    including overlap/warmup contract candles. Google Sheets is therefore an
    auditable mirror of YDB, not a reduced active-contract projection.
    """
    symbol = symbol.strip().upper()
    start = from_date[:10]
    finish = till_date[:10]
    if not symbol:
        raise ValueError("symbol is required")
    if not start or not finish or finish < start:
        raise ValueError("invalid mirror date range")

    session_key = _session_symbol(symbol)
    if not source.is_session_range_verified(session_key, start, finish):
        raise RuntimeError(
            f"D1 session range is not verified for {symbol} {start}..{finish}"
        )

    sessions = source.stored_session_contracts(session_key, start, finish)
    if not sessions:
        raise RuntimeError(f"no stored D1 sessions for {symbol} {start}..{finish}")

    active_pairs = {(day[:10], secid) for day, secid in sessions}
    secids = tuple(sorted({secid for _, secid in sessions}))
    d1_rows: list[list[object]] = []
    observed_pairs: set[tuple[str, str]] = set()

    for secid in secids:
        instrument = source.stored_instrument(secid)
        if instrument is None:
            raise RuntimeError(f"stored instrument metadata missing for {secid}")
        series = source.read(instrument, "D1", start, finish)
        for candle in series.candles:
            day = candle.begin[:10]
            pair = (day, secid)
            if pair in observed_pairs:
                raise RuntimeError(f"duplicate D1 candle for {secid} {day}")
            observed_pairs.add(pair)
            d1_rows.append([
                f"{symbol}|D1|{secid}|{candle.begin}",
                day,
                day,
                "",
                candle.begin,
                candle.end,
                candle.end,
                "INFERRED",
                secid,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                "",
                candle.value,
                candle.completed,
                "D1",
                1,
                series.source,
            ])

    missing_active = active_pairs - observed_pairs
    if missing_active:
        raise RuntimeError(
            "YDB D1/session parity failed before Google sync: "
            f"missing_active={sorted(missing_active)[:5]}"
        )
    if not d1_rows:
        raise RuntimeError(f"no persistent D1 rows for {symbol} {start}..{finish}")

    d1_rows.sort(key=lambda row: (str(row[1]), str(row[8]), str(row[4])))
    session_rows = [[symbol, secid, day] for day, secid in sorted(active_pairs)]
    first_day = str(d1_rows[0][1])
    last_day = str(d1_rows[-1][1])

    sync_rows: list[list[object]] = [
        ["schema", "BIRZHA_MARKET_MIRROR_V2_FULL_PERSISTENT_D1"],
        ["instrument", symbol],
        ["primary_store", "YDB"],
        ["mandatory_mirror", "Google Sheets"],
        ["persistent_timeframe", "D1"],
        ["intraday_mode", "H1/M15 ON_DEMAND_NOT_PERSISTED"],
        ["sync_direction", "YDB → Google Sheets"],
        ["mirror_scope", "FULL_PERSISTENT_CONTRACT_D1"],
        ["mirror_row_count", len(d1_rows)],
        ["active_session_count", len(active_pairs)],
        ["mirror_first_date", first_day],
        ["mirror_last_date", last_day],
        ["contracts_count", len(secids)],
        ["status", "SOURCE_READY_FOR_BRIDGE_PARITY"],
    ]

    sheets: dict[str, list[list[object]]] = {
        "D1": [D1_COLUMNS, *d1_rows],
        "SESSIONS": [SESSION_COLUMNS, *session_rows],
        "VERIFIED_RANGES": [
            VERIFIED_COLUMNS,
            ["YDB_VERIFIED", symbol, "D1", start, finish],
        ],
        "SYNC_STATUS": [SYNC_COLUMNS, *sync_rows],
    }
    return MarketMirrorSnapshot(
        symbol=symbol,
        from_date=first_day,
        till_date=last_day,
        data_rows=len(d1_rows),
        contract_count=len(secids),
        sheets=sheets,
    )
