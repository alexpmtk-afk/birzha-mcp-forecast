from types import SimpleNamespace

import pytest

from birzha.storage.ydb_validation_governance import (
    HoldoutAlreadyConsumedError,
    YdbValidationGovernanceStore,
)


ENGINE = "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS"
DATASET = "dataset-sha256"


class FakePool:
    def __init__(self) -> None:
        self.claims: list[dict[str, str]] = []
        self.retry_idempotent: list[bool] = []
        self.atomic_claim_queries = 0

    def execute_with_retries(self, query, parameters=None, **kwargs):
        retry = kwargs.get("retry_settings")
        if retry is not None:
            self.retry_idempotent.append(bool(getattr(retry, "idempotent", False)))
        compact = " ".join(str(query).split())
        if compact.startswith("CREATE TABLE"):
            return []
        if compact.startswith("DECLARE") and "WHERE NOT EXISTS" in compact:
            self.atomic_claim_queries += 1
            left = parameters["$holdout_start"][0]
            right = parameters["$holdout_end"][0]
            existing = [
                item
                for item in self.claims
                if item["holdout_start"] <= right and item["holdout_end"] >= left
            ]
            if existing:
                return [SimpleNamespace(rows=existing[:1])]
            item = {
                "holdout_start": left,
                "holdout_end": right,
                "protocol": parameters["$protocol"][0],
                "engine_version": parameters["$engine_version"][0],
                "model_fingerprint": parameters["$model_fingerprint"][0],
                "data_fingerprint": parameters["$data_fingerprint"][0],
                "consumed_at": parameters["$consumed_at"][0],
            }
            self.claims.append(item)
            return [SimpleNamespace(rows=[])]
        if compact.startswith("DECLARE") and "SELECT holdout_start" in compact:
            left = parameters["$holdout_start"][0]
            right = parameters["$holdout_end"][0]
            rows = [
                item
                for item in self.claims
                if item["holdout_start"] <= right and item["holdout_end"] >= left
            ]
            return [SimpleNamespace(rows=rows[:1])]
        raise AssertionError(compact)


def _claim(store: YdbValidationGovernanceStore, **overrides):
    values = {
        "holdout_start": "2023-01-01",
        "holdout_end": "2024-12-31",
        "protocol": "TEST",
        "engine_version": ENGINE,
        "model_fingerprint": "abc",
        "data_fingerprint": DATASET,
    }
    values.update(overrides)
    return store.claim_once(**values)


def test_first_holdout_claim_succeeds_and_is_persisted_atomically() -> None:
    pool = FakePool()
    store = YdbValidationGovernanceStore(pool)

    claim = _claim(store)

    assert claim.holdout_start == "2023-01-01"
    assert claim.holdout_end == "2024-12-31"
    assert claim.engine_version == ENGINE
    assert claim.data_fingerprint == DATASET
    assert pool.claims[0]["engine_version"] == ENGINE
    assert pool.claims[0]["model_fingerprint"] == "abc"
    assert pool.claims[0]["data_fingerprint"] == DATASET
    assert pool.retry_idempotent[-1] is False
    assert pool.atomic_claim_queries == 1


def test_same_holdout_cannot_be_claimed_twice() -> None:
    pool = FakePool()
    store = YdbValidationGovernanceStore(pool)
    _claim(store)

    with pytest.raises(HoldoutAlreadyConsumedError):
        _claim(
            store,
            protocol="TEST2",
            engine_version="DIFFERENT_ENGINE",
            model_fingerprint="different",
            data_fingerprint="different-data",
        )

    assert pool.atomic_claim_queries == 2


def test_overlapping_holdout_is_also_blocked() -> None:
    pool = FakePool()
    store = YdbValidationGovernanceStore(pool)
    _claim(store)

    with pytest.raises(HoldoutAlreadyConsumedError):
        _claim(
            store,
            holdout_start="2024-06-01",
            holdout_end="2025-06-01",
            protocol="TEST2",
            engine_version="DIFFERENT_ENGINE",
            model_fingerprint="def",
            data_fingerprint="different-data",
        )

    assert pool.atomic_claim_queries == 2
