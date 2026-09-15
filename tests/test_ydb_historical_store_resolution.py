import json
from types import SimpleNamespace

from birzha.storage.ydb_historical_store import YdbHistoricalCandleStore


class FakePool:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute_with_retries(self, query, parameters=None, **kwargs):
        self.queries.append(query)
        if "SELECT trade_date, secid" in query and "historical_candles_sessions" in query:
            return [
                SimpleNamespace(
                    rows=[
                        {"trade_date": "2025-04-01", "secid": "GDM5"},
                        {"trade_date": "2025-04-02", "secid": "GDM5"},
                        {"trade_date": "2025-06-20", "secid": "GDU5"},
                    ]
                )
            ]
        if "SELECT secid FROM `historical_candles_sessions`" in query:
            return [
                SimpleNamespace(
                    rows=[{"secid": "GDM5"}, {"secid": "GDM5"}]
                )
            ]
        if "SELECT payload_json, source FROM `historical_candles`" in query and "ORDER BY begin DESC LIMIT 1" in query:
            payload = {
                "instrument": {
                    "symbol": "GOLD",
                    "secid": "GDM5",
                    "board": "RFUD",
                    "engine": "futures",
                    "market": "forts",
                    "asset_class": "future",
                    "name": "GOLD-6.25",
                    "root_symbol": "GOLD",
                    "last_trade_date": "2025-06-19",
                    "source": "MOEX_ISS",
                },
                "candle": {},
            }
            return [
                SimpleNamespace(
                    rows=[
                        {
                            "payload_json": json.dumps(payload),
                            "source": "MOEX_ISS",
                        }
                    ]
                )
            ]
        return []


def test_ydb_session_contract_lookup_is_deduplicated_and_exact():
    pool = FakePool()
    store = YdbHistoricalCandleStore(pool)

    secids = store.stored_session_secids(
        "GOLD#ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V1",
        "2025-04-01",
    )

    assert secids == ("GDM5",)
    assert any("trade_date=$trade_date" in query for query in pool.queries)


def test_ydb_bulk_session_contract_lookup_uses_one_range_query():
    pool = FakePool()
    store = YdbHistoricalCandleStore(pool)
    before = len(pool.queries)

    rows = store.stored_session_contracts(
        "GOLD#ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V1",
        "2025-04-01",
        "2025-06-30",
    )

    assert rows == (
        ("2025-04-01", "GDM5"),
        ("2025-04-02", "GDM5"),
        ("2025-06-20", "GDU5"),
    )
    new_queries = pool.queries[before:]
    assert len(new_queries) == 1
    assert "SELECT trade_date, secid" in new_queries[0]
    assert "trade_date>=$from_date" in new_queries[0]
    assert "trade_date<=$till_date" in new_queries[0]


def test_ydb_stored_instrument_recovers_futures_identity_from_candle_payload():
    pool = FakePool()
    store = YdbHistoricalCandleStore(pool)

    instrument = store.stored_instrument("GDM5")

    assert instrument is not None
    assert instrument.symbol == "GOLD"
    assert instrument.secid == "GDM5"
    assert instrument.root_symbol == "GOLD"
    assert instrument.board == "RFUD"
    assert instrument.asset_class == "future"
    assert instrument.last_trade_date == "2025-06-19"


def test_ydb_stored_instrument_missing_row_returns_none():
    class EmptyPool(FakePool):
        def execute_with_retries(self, query, parameters=None, **kwargs):
            self.queries.append(query)
            return []

    store = YdbHistoricalCandleStore(EmptyPool())

    assert store.stored_instrument("MISSING") is None
