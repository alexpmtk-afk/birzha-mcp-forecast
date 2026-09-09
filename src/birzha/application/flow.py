"""Market-flow application service combining ALGOPACK TradeStats and FUTOI."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from json import JSONDecodeError
from typing import Any
from zoneinfo import ZoneInfo

from birzha.application.market_data import MarketDataService
from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.flow import ClientOpenInterest, MarketFlowSnapshot
from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import MoexAnalyticsClient, MoexAnalyticsError


MOEX_TIMEZONE = ZoneInfo("Europe/Moscow")


@dataclass(slots=True)
class MarketFlowService:
    market_data: MarketDataService
    analytics: MoexAnalyticsClient
    historical: HistoricalFlowDataService | None = None

    @classmethod
    def default(
        cls,
        *,
        control_plane: ProcessUpstreamControlPlane | None = None,
    ) -> "MarketFlowService":
        shared_control = control_plane or ProcessUpstreamControlPlane()
        market_data = MarketDataService.default(control_plane=shared_control)
        return cls(
            market_data=market_data,
            analytics=MoexAnalyticsClient(control_plane=shared_control),
        )

    def build(
        self,
        symbol: str,
        *,
        from_date: str | None = None,
        till_date: str | None = None,
        lookback_days: int = 5,
        cutoff_at: str | None = None,
    ) -> MarketFlowSnapshot:
        cutoff = _parse_timestamp(cutoff_at) if cutoff_at else None
        if cutoff_at and cutoff is None:
            raise ValueError("cutoff_at must be a parseable exchange timestamp")
        resolve_as_of = cutoff.date() if cutoff is not None else date.fromisoformat(till_date) if till_date else None
        instrument = self.market_data.resolve(symbol, as_of=resolve_as_of)
        return self.build_for_instrument(
            instrument,
            from_date=from_date,
            till_date=till_date,
            lookback_days=lookback_days,
            cutoff_at=cutoff_at,
        )

    def build_for_instrument(
        self,
        instrument: Instrument,
        *,
        from_date: str | None = None,
        till_date: str | None = None,
        lookback_days: int = 5,
        cutoff_at: str | None = None,
    ) -> MarketFlowSnapshot:
        """Build flow for the exact resolved contract using only data <= T0.

        ``cutoff_at`` is the causal forecast T0. Rows whose exchange timestamp is
        later than T0 are excluded. Rows without a parseable timestamp are also
        excluded when a cutoff is requested; unknown time is never assumed safe.
        Optional analytical feeds fail closed to missing/DEGRADED rather than
        destroying the price-based snapshot when MOEX returns malformed data.
        """

        if lookback_days <= 0:
            raise ValueError("lookback_days must be > 0")
        cutoff = _parse_timestamp(cutoff_at) if cutoff_at else None
        if cutoff_at and cutoff is None:
            raise ValueError("cutoff_at must be a parseable exchange timestamp")

        default_till = cutoff.date() if cutoff is not None else datetime.now(MOEX_TIMEZONE).date()
        till = date.fromisoformat(till_date) if till_date else default_till
        start = date.fromisoformat(from_date) if from_date else till - timedelta(days=lookback_days)
        if start > till:
            raise ValueError("from_date must not be after till_date")

        warnings: list[str] = []
        try:
            trade_rows = (
                self.historical.tradestats(instrument, from_date=start.isoformat(), till_date=till.isoformat())
                if self.historical is not None
                else self.analytics.fetch_tradestats(instrument, from_date=start.isoformat(), till_date=till.isoformat())
            )
        except (MoexAnalyticsError, JSONDecodeError) as exc:
            trade_rows = []
            warnings.append(
                f"ALGOPACK_TRADESTATS_UNAVAILABLE:{type(exc).__name__}:{exc}"
            )

        try:
            futoi_rows = (
                self.historical.futoi(instrument, from_date=start.isoformat(), till_date=till.isoformat())
                if self.historical is not None
                else self.analytics.fetch_futoi(instrument, from_date=start.isoformat(), till_date=till.isoformat())
            )
        except (MoexAnalyticsError, JSONDecodeError) as exc:
            futoi_rows = []
            warnings.append(f"FUTOI_UNAVAILABLE:{type(exc).__name__}:{exc}")

        if cutoff is not None:
            trade_rows = _causal_rows(trade_rows, cutoff)
            futoi_rows = _causal_rows(futoi_rows, cutoff)

        if not trade_rows:
            warnings.append("ALGOPACK_TRADESTATS_EMPTY")
        if not futoi_rows:
            warnings.append("FUTOI_EMPTY")

        buy_volume = _sum_field(trade_rows, "vol_b")
        sell_volume = _sum_field(trade_rows, "vol_s")
        buy_value = _sum_field(trade_rows, "val_b")
        sell_value = _sum_field(trade_rows, "val_s")
        volume_delta = _difference(buy_volume, sell_volume)
        value_delta = _difference(buy_value, sell_value)
        total_aggressive_volume = _add(buy_volume, sell_volume)
        delta_ratio = (
            volume_delta / total_aggressive_volume
            if volume_delta is not None and total_aggressive_volume not in {None, 0.0}
            else None
        )

        ordered_trade_rows = sorted(trade_rows, key=_row_time_key)
        first_open = _first_number(ordered_trade_rows, "pr_open")
        last_close = _last_number(ordered_trade_rows, "pr_close")
        price_change_pct = (
            (last_close / first_open - 1.0) * 100.0
            if first_open not in {None, 0.0} and last_close is not None
            else None
        )
        oi_open = _first_number(ordered_trade_rows, "oi_open")
        oi_close = _last_number(ordered_trade_rows, "oi_close")
        oi_change = _difference(oi_close, oi_open)

        latest_by_group = _latest_futoi_by_group(futoi_rows)
        individuals = _client_oi(latest_by_group.get("FIZ"), "FIZ")
        legal_entities = _client_oi(latest_by_group.get("YUR"), "YUR")
        observed = [dt for row in [*trade_rows, *futoi_rows] if (dt := _row_datetime(row)) is not None]
        as_of = max(observed).isoformat() if observed else None
        if cutoff is not None and as_of is not None:
            parsed_as_of = _parse_timestamp(as_of)
            assert parsed_as_of is not None and parsed_as_of <= cutoff

        quality = "PASS" if not warnings else "DEGRADED"
        return MarketFlowSnapshot(
            symbol=instrument.symbol,
            secid=instrument.secid,
            from_date=start.isoformat(),
            till_date=till.isoformat(),
            as_of=as_of,
            source="MOEX_ALGOPACK+FUTOI",
            intervals=len(trade_rows),
            buy_volume=_round_or_none(buy_volume),
            sell_volume=_round_or_none(sell_volume),
            volume_delta=_round_or_none(volume_delta),
            volume_delta_ratio=_round_or_none(delta_ratio, 6),
            buy_value=_round_or_none(buy_value),
            sell_value=_round_or_none(sell_value),
            value_delta=_round_or_none(value_delta),
            price_change_pct=_round_or_none(price_change_pct, 6),
            algopack_oi_open=_round_or_none(oi_open),
            algopack_oi_close=_round_or_none(oi_close),
            algopack_oi_change=_round_or_none(oi_change),
            individuals=individuals,
            legal_entities=legal_entities,
            data_quality=quality,
            warnings=tuple(warnings),
        )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MOEX_TIMEZONE)
    return parsed.astimezone(MOEX_TIMEZONE)


def _first_present(row: dict[str, Any], *keys: str) -> object | None:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _row_datetime(row: dict[str, Any]) -> datetime | None:
    tradedate = str(_first_present(row, "tradedate", "TRADEDATE") or "").strip()
    tradetime = str(_first_present(row, "tradetime", "TRADETIME") or "").strip()
    if tradedate and tradetime:
        parsed = _parse_timestamp(f"{tradedate}T{tradetime}")
        if parsed is not None:
            return parsed
    for key in ("systime", "SYSTIME", "timestamp", "TIMESTAMP"):
        value = _first_present(row, key)
        parsed = _parse_timestamp(str(value)) if value is not None else None
        if parsed is not None:
            return parsed
    return None


def _causal_rows(rows: list[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    return [row for row in rows if (observed := _row_datetime(row)) is not None and observed <= cutoff]


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int_or_none(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _sum_field(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [value for row in rows if (value := _number(_first_present(row, field, field.upper()))) is not None]
    return sum(values) if values else None


def _first_number(rows: list[dict[str, Any]], field: str) -> float | None:
    for row in rows:
        value = _number(_first_present(row, field, field.upper()))
        if value is not None:
            return value
    return None


def _last_number(rows: list[dict[str, Any]], field: str) -> float | None:
    for row in reversed(rows):
        value = _number(_first_present(row, field, field.upper()))
        if value is not None:
            return value
    return None


def _difference(left: float | None, right: float | None) -> float | None:
    return left - right if left is not None and right is not None else None


def _add(left: float | None, right: float | None) -> float | None:
    return left + right if left is not None and right is not None else None


def _row_time_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(_first_present(row, "tradedate", "TRADEDATE") or ""),
        str(_first_present(row, "tradetime", "TRADETIME", "SYSTIME", "systime") or ""),
        _int_or_none(_first_present(row, "seqnum", "SEQNUM")) or 0,
    )


def _latest_futoi_by_group(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=_row_time_key):
        group = str(_first_present(row, "clgroup", "CLGROUP") or "").upper()
        if group in {"FIZ", "YUR"}:
            result[group] = row
    return result


def _client_oi(row: dict[str, Any] | None, group: str) -> ClientOpenInterest | None:
    if row is None:
        return None
    observed = _row_datetime(row)
    return ClientOpenInterest(
        client_group=group,
        net_position=_round_or_none(_number(_first_present(row, "pos", "POS"))),
        long_position=_round_or_none(_number(_first_present(row, "pos_long", "POS_LONG"))),
        short_position=_round_or_none(_number(_first_present(row, "pos_short", "POS_SHORT"))),
        long_accounts=_int_or_none(_first_present(row, "pos_long_num", "POS_LONG_NUM")),
        short_accounts=_int_or_none(_first_present(row, "pos_short_num", "POS_SHORT_NUM")),
        observed_at=observed.isoformat() if observed else None,
    )


def _round_or_none(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None
