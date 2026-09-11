from types import SimpleNamespace

import pytest

from birzha.application.validation_dataset_fingerprint import (
    build_validation_dataset_fingerprint,
)
from birzha.domain.market import Instrument


SBER = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
    source="TEST",
)


class FakeStore:
    def __init__(self, *, sessions=True) -> None:
        self.sessions = sessions

    def stored_session_contracts(self, symbol, from_date, till_date):
        if not self.sessions:
            return ()
        return (("2023-01-03", "SBER"),)

    def stored_instrument(self, secid):
        return SBER if secid == "SBER" else None


class FakeHistory:
    def __init__(self, store) -> None:
        self.store = store


class FakePool:
    def __init__(self, *, price_payload="price-v1", flow_payload="flow-v1") -> None:
        self.price_payload = price_payload
        self.flow_payload = flow_payload

    def execute_with_retries(self, query, parameters=None, **kwargs):
        compact = " ".join(str(query).split())
        if "FROM `historical_candles`" in compact:
            return [
                SimpleNamespace(
                    rows=[
                        {
                            "begin": "2023-01-03T10:00:00",
                            "end_time": "2023-01-03T23:59:59",
                            "payload_json": self.price_payload,
                            "source": "TEST_PRICE",
                        }
                    ]
                )
            ]
        if "FROM `historical_flow_rows`" in compact:
            return [
                SimpleNamespace(
                    rows=[
                        {
                            "trade_date": "2023-01-03",
                            "row_key": "1",
                            "payload_json": self.flow_payload,
                            "source": "TEST_FLOW",
                        }
                    ]
                )
            ]
        raise AssertionError(compact)


def _fingerprint(pool: FakePool, *, sessions=True):
    return build_validation_dataset_fingerprint(
        pool,
        FakeHistory(FakeStore(sessions=sessions)),  # type: ignore[arg-type]
        ("SBER",),
        validation_start="2023-01-01",
        validation_end="2023-12-31",
    )


def test_same_frozen_dataset_has_stable_fingerprint() -> None:
    first = _fingerprint(FakePool())
    second = _fingerprint(FakePool())

    assert first.sha256 == second.sha256
    assert first.contract_sessions == 1
    assert first.price_rows == 3
    assert first.flow_rows == 1


def test_price_change_changes_dataset_fingerprint() -> None:
    baseline = _fingerprint(FakePool(price_payload="price-v1"))
    changed = _fingerprint(FakePool(price_payload="price-v2"))

    assert baseline.sha256 != changed.sha256


def test_optional_flow_change_also_changes_dataset_fingerprint() -> None:
    baseline = _fingerprint(FakePool(flow_payload="flow-v1"))
    changed = _fingerprint(FakePool(flow_payload="flow-v2"))

    assert baseline.sha256 != changed.sha256


def test_missing_session_contract_map_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="no stored session-contract map"):
        _fingerprint(FakePool(), sessions=False)
