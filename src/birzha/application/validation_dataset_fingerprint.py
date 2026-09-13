"""Cryptographic identity of the frozen durable validation dataset.

The durable dataset contains completed D1 price history plus optional historical
flow rows. H1/M15 are intentionally excluded because they are contextual,
on-demand inputs fetched for each T0 and are never stored in shared candle
history.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

import ydb

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.historical_flow import (
    TRADESTATS_ASSET_CLASSES,
    _verification_dataset,
)
from birzha.application.market_data import is_futures_root_symbol


PRICE_LOOKBACK_DAYS = {"D1": 300}
FLOW_LOOKBACK_DAYS = 10


class QueryPool(Protocol):
    def execute_with_retries(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
        **kwargs: object,
    ): ...


@dataclass(frozen=True, slots=True)
class ValidationDatasetFingerprint:
    sha256: str
    contract_sessions: int
    price_rows: int
    flow_rows: int

    def to_dict(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "contract_sessions": self.contract_sessions,
            "price_rows": self.price_rows,
            "flow_rows": self.flow_rows,
            "persistent_price_timeframes": ["D1"],
            "intraday_mode": "ON_DEMAND_NOT_PERSISTED",
        }


def build_validation_dataset_fingerprint(
    pool: QueryPool,
    history: HistoricalDataService,
    symbols: tuple[str, ...],
    *,
    validation_start: str,
    validation_end: str,
    candle_table: str = "historical_candles",
    flow_table: str = "historical_flow_rows",
    flow_verified_table: str = "historical_flow_rows_verified",
) -> ValidationDatasetFingerprint:
    """Hash exact durable D1 inputs and optional-flow verification state."""
    if not symbols:
        raise ValueError("at least one symbol is required")
    start = date.fromisoformat(validation_start[:10])
    end = date.fromisoformat(validation_end[:10])
    if start > end:
        raise ValueError("validation_start must not be after validation_end")
    candle_table = _safe_table_name(candle_table)
    flow_table = _safe_table_name(flow_table)
    flow_verified_table = _safe_table_name(flow_verified_table)

    hasher = hashlib.sha256()
    contract_sessions = 0
    price_rows = 0
    flow_rows = 0
    unique_instruments: dict[str, object] = {}

    _feed(hasher, ["FINGERPRINT_VERSION", "M23_DATASET_SHA256_V3_D1_ONLY"])
    _feed(hasher, ["VALIDATION_RANGE", start.isoformat(), end.isoformat()])
    _feed(hasher, ["INTRADAY_MODE", "ON_DEMAND_NOT_PERSISTED"])

    for symbol in symbols:
        session_symbol = _verification_symbol(
            symbol,
            "D1",
            is_root=is_futures_root_symbol(symbol),
        )
        rows = history.store.stored_session_contracts(
            session_symbol, start.isoformat(), end.isoformat()
        )
        if not rows:
            raise RuntimeError(
                f"no stored session-contract map for dataset fingerprint: {symbol}"
            )
        _feed(hasher, ["SYMBOL", symbol, session_symbol])
        for trade_date, secid in rows:
            contract_sessions += 1
            _feed(hasher, ["SESSION", symbol, str(trade_date)[:10], str(secid)])
            if secid not in unique_instruments:
                instrument = history.store.stored_instrument(str(secid))
                if instrument is None:
                    raise RuntimeError(
                        f"stored instrument missing during dataset fingerprint: {secid}"
                    )
                unique_instruments[str(secid)] = instrument

    for secid in sorted(unique_instruments):
        instrument = unique_instruments[secid]
        instrument_payload = (
            instrument.to_dict()  # type: ignore[attr-defined]
            if hasattr(instrument, "to_dict")
            else {
                "symbol": getattr(instrument, "symbol", ""),
                "secid": secid,
                "root_symbol": getattr(instrument, "root_symbol", None),
                "board": getattr(instrument, "board", ""),
                "engine": getattr(instrument, "engine", ""),
                "market": getattr(instrument, "market", ""),
                "asset_class": getattr(instrument, "asset_class", "unknown"),
            }
        )
        _feed(hasher, ["INSTRUMENT", secid, instrument_payload])
        timeframe = "D1"
        left = start - timedelta(days=PRICE_LOOKBACK_DAYS[timeframe])
        rows = _price_rows(
            pool,
            candle_table=candle_table,
            secid=secid,
            timeframe=timeframe,
            from_date=left.isoformat(),
            till_date=end.isoformat(),
        )
        _feed(hasher, ["PRICE_SERIES", secid, timeframe, len(rows)])
        for row in rows:
            price_rows += 1
            _feed(
                hasher,
                [
                    "PRICE",
                    secid,
                    timeframe,
                    _row_value(row, "begin"),
                    _row_value(row, "end_time"),
                    _row_value(row, "payload_json"),
                    _row_value(row, "source"),
                ],
            )

    flow_left = (start - timedelta(days=FLOW_LOOKBACK_DAYS)).isoformat()
    flow_keys: set[tuple[str, str]] = set()
    for secid, instrument in unique_instruments.items():
        asset_class = str(getattr(instrument, "asset_class", "unknown"))
        if asset_class in TRADESTATS_ASSET_CLASSES:
            flow_keys.add(("TRADESTATS", secid))
        if asset_class == "future":
            root = str(
                getattr(instrument, "root_symbol", None)
                or getattr(instrument, "symbol", "")
            ).strip()
            if root:
                flow_keys.add(("FUTOI", root))

    for dataset, key in sorted(flow_keys):
        verification_dataset = _verification_dataset(dataset)
        verification_rows = _flow_verification_rows(
            pool,
            verified_table=flow_verified_table,
            dataset=verification_dataset,
            key=key,
            from_date=flow_left,
            till_date=end.isoformat(),
        )
        _feed(
            hasher,
            [
                "FLOW_VERIFICATION_SERIES",
                verification_dataset,
                key,
                len(verification_rows),
            ],
        )
        for row in verification_rows:
            _feed(
                hasher,
                [
                    "FLOW_VERIFICATION",
                    verification_dataset,
                    key,
                    _row_value(row, "from_date"),
                    _row_value(row, "till_date"),
                ],
            )

        rows = _flow_rows(
            pool,
            flow_table=flow_table,
            dataset=dataset,
            key=key,
            from_date=flow_left,
            till_date=end.isoformat(),
        )
        _feed(hasher, ["FLOW_SERIES", dataset, key, len(rows)])
        for row in rows:
            flow_rows += 1
            _feed(
                hasher,
                [
                    "FLOW",
                    dataset,
                    key,
                    _row_value(row, "trade_date"),
                    _row_value(row, "row_key"),
                    _row_value(row, "payload_json"),
                    _row_value(row, "source"),
                ],
            )

    return ValidationDatasetFingerprint(
        sha256=hasher.hexdigest(),
        contract_sessions=contract_sessions,
        price_rows=price_rows,
        flow_rows=flow_rows,
    )


def _price_rows(
    pool: QueryPool,
    *,
    candle_table: str,
    secid: str,
    timeframe: str,
    from_date: str,
    till_date: str,
) -> list[object]:
    result = pool.execute_with_retries(
        f"""
        DECLARE $secid AS Utf8;
        DECLARE $timeframe AS Utf8;
        DECLARE $from_date AS Utf8;
        DECLARE $upper AS Utf8;
        SELECT begin, end_time, payload_json, source
        FROM `{candle_table}`
        WHERE secid=$secid AND timeframe=$timeframe
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
    return _rows(result)


def _flow_rows(
    pool: QueryPool,
    *,
    flow_table: str,
    dataset: str,
    key: str,
    from_date: str,
    till_date: str,
) -> list[object]:
    result = pool.execute_with_retries(
        f"""
        DECLARE $dataset AS Utf8;
        DECLARE $key AS Utf8;
        DECLARE $from_date AS Utf8;
        DECLARE $till_date AS Utf8;
        SELECT trade_date, row_key, payload_json, source
        FROM `{flow_table}`
        WHERE dataset=$dataset AND key_symbol=$key
          AND trade_date >= $from_date AND trade_date <= $till_date
        ORDER BY trade_date, row_key;
        """,
        {
            "$dataset": _utf8(dataset),
            "$key": _utf8(key),
            "$from_date": _utf8(from_date),
            "$till_date": _utf8(till_date),
        },
        retry_settings=ydb.RetrySettings(idempotent=True),
    )
    return _rows(result)


def _flow_verification_rows(
    pool: QueryPool,
    *,
    verified_table: str,
    dataset: str,
    key: str,
    from_date: str,
    till_date: str,
) -> list[object]:
    result = pool.execute_with_retries(
        f"""
        DECLARE $dataset AS Utf8;
        DECLARE $key AS Utf8;
        DECLARE $from_date AS Utf8;
        DECLARE $till_date AS Utf8;
        SELECT from_date, till_date
        FROM `{verified_table}`
        WHERE dataset=$dataset AND key_symbol=$key
          AND from_date <= $till_date AND till_date >= $from_date
        ORDER BY from_date, till_date;
        """,
        {
            "$dataset": _utf8(dataset),
            "$key": _utf8(key),
            "$from_date": _utf8(from_date),
            "$till_date": _utf8(till_date),
        },
        retry_settings=ydb.RetrySettings(idempotent=True),
    )
    return _rows(result)


def _feed(hasher: object, value: list[object]) -> None:
    payload = json.dumps(
        value,
        sort_keys=False,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    hasher.update(len(payload).to_bytes(8, "big"))  # type: ignore[attr-defined]
    hasher.update(payload)  # type: ignore[attr-defined]


def _utf8(value: str):
    return (value, ydb.PrimitiveType.Utf8)


def _rows(result: object) -> list[object]:
    if result is None:
        return []
    sets = list(result) if not isinstance(result, list) else result
    rows: list[object] = []
    for result_set in sets:
        rows.extend(list(getattr(result_set, "rows", []) or []))
    return rows


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
