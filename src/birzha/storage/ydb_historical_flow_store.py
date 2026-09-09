"""Distributed YDB storage for raw TradeStats and FUTOI history."""

from __future__ import annotations

import json
from typing import Protocol

import ydb

from birzha.storage.historical_flow_store import _row_key, _trade_date


class QueryPool(Protocol):
    def execute_with_retries(self, query: str, parameters: dict[str, object] | None = None, **kwargs: object): ...


class YdbHistoricalFlowStore:
    storage_scope = "distributed"

    def __init__(self, pool: QueryPool, *, table: str = "historical_flow_rows") -> None:
        self._pool = pool
        self._table = _safe_table_name(table)
        self._verified_table = _safe_table_name(table + "_verified")
        self._init_schema()

    def _init_schema(self) -> None:
        self._pool.execute_with_retries(f"""
            CREATE TABLE IF NOT EXISTS `{self._table}` (
                dataset Utf8 NOT NULL,
                key_symbol Utf8 NOT NULL,
                row_key Utf8 NOT NULL,
                trade_date Utf8 NOT NULL,
                payload_json Utf8 NOT NULL,
                source Utf8 NOT NULL,
                PRIMARY KEY (dataset,key_symbol,row_key)
            );
        """, retry_settings=ydb.RetrySettings(idempotent=True))
        self._pool.execute_with_retries(f"""
            CREATE TABLE IF NOT EXISTS `{self._verified_table}` (
                dataset Utf8 NOT NULL,
                key_symbol Utf8 NOT NULL,
                from_date Utf8 NOT NULL,
                till_date Utf8 NOT NULL,
                PRIMARY KEY (dataset,key_symbol,from_date,till_date)
            );
        """, retry_settings=ydb.RetrySettings(idempotent=True))

    def upsert_rows(self, dataset: str, key: str, rows: list[dict[str, object]], source: str) -> int:
        if not rows:
            return 0
        row_type=(ydb.StructType()
            .add_member("dataset",ydb.PrimitiveType.Utf8)
            .add_member("key_symbol",ydb.PrimitiveType.Utf8)
            .add_member("row_key",ydb.PrimitiveType.Utf8)
            .add_member("trade_date",ydb.PrimitiveType.Utf8)
            .add_member("payload_json",ydb.PrimitiveType.Utf8)
            .add_member("source",ydb.PrimitiveType.Utf8))
        list_type=ydb.ListType(row_type)
        materialized=[]
        for row in rows:
            payload=json.dumps(row,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
            materialized.append({"dataset":dataset,"key_symbol":key,"row_key":_row_key(row,payload),"trade_date":_trade_date(row),"payload_json":payload,"source":source})
        self._pool.execute_with_retries(f"""
            DECLARE $rows AS List<Struct<dataset:Utf8,key_symbol:Utf8,row_key:Utf8,trade_date:Utf8,payload_json:Utf8,source:Utf8>>;
            UPSERT INTO `{self._table}` SELECT dataset,key_symbol,row_key,trade_date,payload_json,source FROM AS_TABLE($rows);
        """,{"$rows":(materialized,list_type)},retry_settings=ydb.RetrySettings(idempotent=True))
        return len(materialized)

    def read_rows(self, dataset: str, key: str, from_date: str, till_date: str) -> list[dict[str, object]]:
        result=self._pool.execute_with_retries(f"""
            DECLARE $dataset AS Utf8; DECLARE $key AS Utf8; DECLARE $from AS Utf8; DECLARE $till AS Utf8;
            SELECT payload_json FROM `{self._table}` WHERE dataset=$dataset AND key_symbol=$key AND trade_date>=$from AND trade_date<=$till ORDER BY trade_date,row_key;
        """,{"$dataset":_utf8(dataset),"$key":_utf8(key),"$from":_utf8(from_date),"$till":_utf8(till_date)},retry_settings=ydb.RetrySettings(idempotent=True))
        return [json.loads(str(_row_value(row,"payload_json"))) for row in _rows(result)]

    def is_verified(self, dataset: str, key: str, from_date: str, till_date: str) -> bool:
        result=self._pool.execute_with_retries(f"""
            DECLARE $dataset AS Utf8; DECLARE $key AS Utf8; DECLARE $from AS Utf8; DECLARE $till AS Utf8;
            SELECT 1 AS found FROM `{self._verified_table}` WHERE dataset=$dataset AND key_symbol=$key AND from_date<=$from AND till_date>=$till LIMIT 1;
        """,{"$dataset":_utf8(dataset),"$key":_utf8(key),"$from":_utf8(from_date),"$till":_utf8(till_date)},retry_settings=ydb.RetrySettings(idempotent=True))
        return _first_row(result) is not None

    def mark_verified(self, dataset: str, key: str, from_date: str, till_date: str) -> None:
        self._pool.execute_with_retries(f"""
            DECLARE $dataset AS Utf8; DECLARE $key AS Utf8; DECLARE $from AS Utf8; DECLARE $till AS Utf8;
            UPSERT INTO `{self._verified_table}` (dataset,key_symbol,from_date,till_date) VALUES ($dataset,$key,$from,$till);
        """,{"$dataset":_utf8(dataset),"$key":_utf8(key),"$from":_utf8(from_date),"$till":_utf8(till_date)},retry_settings=ydb.RetrySettings(idempotent=True))

    def close(self) -> None:
        return None


def _utf8(value: str):
    return (value,ydb.PrimitiveType.Utf8)


def _rows(result: object) -> list[object]:
    if result is None:
        return []
    sets=list(result) if not isinstance(result,list) else result
    rows=[]
    for result_set in sets:
        rows.extend(list(getattr(result_set,"rows",[]) or []))
    return rows


def _first_row(result: object) -> object | None:
    rows=_rows(result)
    return rows[0] if rows else None


def _row_value(row: object, field: str) -> object:
    if isinstance(row,dict):
        return row[field]
    try:
        return row[field]
    except (TypeError,KeyError):
        return getattr(row,field)


def _safe_table_name(value: str) -> str:
    if not value or any(not (char.isalnum() or char == "_") for char in value):
        raise ValueError("YDB table name must contain only letters, digits and underscore")
    return value
