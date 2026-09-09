"""Distributed YDB historical candle storage."""

from __future__ import annotations

import json
from typing import Protocol

import ydb

from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import HistoricalCoverage


class QueryPool(Protocol):
    def execute_with_retries(self, query: str, parameters: dict[str, object] | None = None, **kwargs: object): ...


class YdbHistoricalCandleStore:
    """Durable multi-instance historical store used by the remote MCP runtime."""

    storage_scope = "distributed"

    def __init__(self, pool: QueryPool, *, table: str = "historical_candles") -> None:
        self._pool = pool
        self._table = _safe_table_name(table)
        self._init_schema()

    def _init_schema(self) -> None:
        self._pool.execute_with_retries(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._table}` (
                secid Utf8 NOT NULL,
                timeframe Utf8 NOT NULL,
                begin Utf8 NOT NULL,
                end_time Utf8 NOT NULL,
                payload_json Utf8 NOT NULL,
                source Utf8 NOT NULL,
                PRIMARY KEY (secid, timeframe, begin)
            );
            """,
            retry_settings=ydb.RetrySettings(idempotent=True),
        )

    def upsert_series(self, series: CandleSeries) -> int:
        if not series.candles:
            return 0
        row_type = (
            ydb.StructType()
            .add_member("secid", ydb.PrimitiveType.Utf8)
            .add_member("timeframe", ydb.PrimitiveType.Utf8)
            .add_member("begin", ydb.PrimitiveType.Utf8)
            .add_member("end_time", ydb.PrimitiveType.Utf8)
            .add_member("payload_json", ydb.PrimitiveType.Utf8)
            .add_member("source", ydb.PrimitiveType.Utf8)
        )
        list_type = ydb.ListType(row_type)
        rows = []
        for candle in series.candles:
            payload = {
                "instrument": series.instrument.to_dict(),
                "candle": candle.to_dict(),
            }
            rows.append({
                "secid": series.instrument.secid,
                "timeframe": series.timeframe,
                "begin": candle.begin,
                "end_time": candle.end,
                "payload_json": json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                ),
                "source": series.source,
            })
        self._pool.execute_with_retries(
            f"""
            DECLARE $rows AS List<Struct<
                secid:Utf8,timeframe:Utf8,begin:Utf8,end_time:Utf8,payload_json:Utf8,source:Utf8
            >>;
            UPSERT INTO `{self._table}`
            SELECT secid, timeframe, begin, end_time, payload_json, source FROM AS_TABLE($rows);
            """,
            {"$rows": (rows, list_type)},
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        return len(rows)

    def coverage(self, secid: str, timeframe: str) -> HistoricalCoverage:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $secid AS Utf8;
            DECLARE $timeframe AS Utf8;
            SELECT MIN(begin) AS first_begin, MAX(end_time) AS last_end, COUNT(*) AS cnt
            FROM `{self._table}`
            WHERE secid = $secid AND timeframe = $timeframe;
            """,
            {"$secid": _utf8(secid), "$timeframe": _utf8(timeframe)},
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        row = _first_row(result)
        if row is None:
            return HistoricalCoverage(secid, timeframe, None, None, 0)
        count = int(_row_value(row, "cnt") or 0)
        if count == 0:
            return HistoricalCoverage(secid, timeframe, None, None, 0)
        return HistoricalCoverage(
            secid,
            timeframe,
            str(_row_value(row, "first_begin")),
            str(_row_value(row, "last_end")),
            count,
        )

    def read(self, instrument: Instrument, timeframe: str, from_date: str, till_date: str) -> CandleSeries:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $secid AS Utf8;
            DECLARE $timeframe AS Utf8;
            DECLARE $from_date AS Utf8;
            DECLARE $upper AS Utf8;
            SELECT payload_json, source FROM `{self._table}`
            WHERE secid = $secid AND timeframe = $timeframe
              AND begin >= $from_date AND begin < $upper
            ORDER BY begin;
            """,
            {
                "$secid": _utf8(instrument.secid),
                "$timeframe": _utf8(timeframe),
                "$from_date": _utf8(from_date),
                "$upper": _utf8(_exclusive_upper_bound(till_date)),
            },
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        rows = _rows(result)
        candles = []
        source = instrument.source
        for row in rows:
            payload = json.loads(str(_row_value(row, "payload_json")))
            data = payload["candle"]
            candles.append(Candle(
                open=_float_or_none(data.get("open")),
                close=_float_or_none(data.get("close")),
                high=_float_or_none(data.get("high")),
                low=_float_or_none(data.get("low")),
                value=_float_or_none(data.get("value")),
                volume=_float_or_none(data.get("volume")),
                begin=str(data["begin"]),
                end=str(data["end"]),
                completed=bool(data.get("completed", True)),
            ))
            source = str(_row_value(row, "source"))
        return CandleSeries(instrument=instrument, timeframe=timeframe, candles=tuple(candles), source=source)

    def stored_trade_dates(self, secid: str, timeframe: str, from_date: str, till_date: str) -> tuple[str, ...]:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $secid AS Utf8;
            DECLARE $timeframe AS Utf8;
            DECLARE $from_date AS Utf8;
            DECLARE $upper AS Utf8;
            SELECT begin FROM `{self._table}`
            WHERE secid = $secid AND timeframe = $timeframe
              AND begin >= $from_date AND begin < $upper
            ORDER BY begin;
            """,
            {
                "$secid": _utf8(secid),
                "$timeframe": _utf8(timeframe),
                "$from_date": _utf8(from_date),
                "$upper": _utf8(_exclusive_upper_bound(till_date)),
            },
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        return tuple(sorted({str(_row_value(row, "begin"))[:10] for row in _rows(result)}))

    def close(self) -> None:
        return None


def _utf8(value: str):
    return (value, ydb.PrimitiveType.Utf8)


def _rows(result: object) -> list[object]:
    if result is None:
        return []
    result_sets = list(result) if not isinstance(result, list) else result
    rows: list[object] = []
    for result_set in result_sets:
        rows.extend(list(getattr(result_set, "rows", []) or []))
    return rows


def _first_row(result: object) -> object | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _row_value(row: object, field: str) -> object:
    if isinstance(row, dict):
        return row[field]
    try:
        return row[field]  # type: ignore[index]
    except (TypeError, KeyError):
        return getattr(row, field)


def _safe_table_name(value: str) -> str:
    if not value or any(not (char.isalnum() or char == "_") for char in value):
        raise ValueError("YDB table name must contain only letters, digits and underscore")
    return value


def _exclusive_upper_bound(till_date: str) -> str:
    return till_date + "T23:59:59.999999" if len(till_date) == 10 else till_date


def _float_or_none(value: object) -> float | None:
    return None if value is None else float(value)
