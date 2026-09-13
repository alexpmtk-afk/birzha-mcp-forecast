from __future__ import annotations

import inspect

import scripts.run_authorized_ydb_prepare_validation as prepare
from birzha.application.validation_readiness import required_price_ranges


def test_data_preparation_is_d1_only_and_not_model_capacity_gate() -> None:
    ranges = required_price_ranges("2021-01-01", "2024-12-31")

    assert [item[0] for item in ranges] == ["D1"]
    assert not hasattr(prepare, "_session_capacity")


def test_data_preparation_cannot_emit_old_insufficient_data_capacity_status() -> None:
    source = inspect.getsource(prepare.main)

    assert "INSUFFICIENT_DATA" not in source
    # H1/M15 may be mentioned in comments/help text to state that they are
    # intentionally on-demand. What must never reappear is executable durable
    # preparation/sync for those timeframes.
    assert 'timeframe="H1"' not in source
    assert 'timeframe="M15"' not in source
    assert 'timeframe="D1"' in source
