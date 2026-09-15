from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.validation_capacity import stored_contract_capacity
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


START = date(2021, 1, 1)


def _history(symbol: str, secids: list[str]) -> tuple[HistoricalDataService, DuckDBHistoricalCandleStore]:
    store = DuckDBHistoricalCandleStore(":memory:")
    is_root = symbol.upper() in {"SI", "BR", "GOLD"}
    session_symbol = _verification_symbol(symbol, "D1", is_root=is_root)
    for index, secid in enumerate(secids):
        trade_date = (START + timedelta(days=index)).isoformat()
        store.record_sessions(session_symbol, secid, (trade_date,))
    end = (START + timedelta(days=len(secids) - 1)).isoformat()
    store.mark_session_range_verified(session_symbol, START.isoformat(), end)
    history = HistoricalDataService(
        market_data=SimpleNamespace(direct_resolver=object()),
        store=store,
    )
    return history, store


def _capacity(symbol: str, secids: list[str]):
    history, store = _history(symbol, secids)
    try:
        end = (START + timedelta(days=len(secids) - 1)).isoformat()
        return stored_contract_capacity(
            history,
            symbol,
            from_date=START.isoformat(),
            till_date=end,
            step_sessions=5,
            max_points=80,
        )
    finally:
        store.close()


def test_direct_instrument_can_supply_twenty_long_horizon_samples() -> None:
    result = _capacity("SBER", ["SBER"] * 500)

    assert result.sessions == 500
    assert result.mature_raw_candidates == 80
    assert result.non_overlapping_observations == {5: 80, 10: 40, 20: 20}


def test_quarterly_rolls_reduce_real_mature_sample_capacity() -> None:
    secids: list[str] = []
    for contract_index in range(8):
        secids.extend([f"GD{contract_index}"] * 60)
    secids.extend(["GD8"] * 20)

    result = _capacity("GOLD", secids)

    assert result.sessions == 500
    assert result.mature_raw_candidates == 64
    assert result.non_overlapping_observations[20] == 16


def test_sparse_roll_safe_windows_are_not_thinned_twice() -> None:
    # Only indexes 0 and 25 can mature through the 20-session horizon. Their
    # windows are already disjoint, so both are independent observations even
    # though the nominal T0 cadence is five sessions.
    secids = ["A"] * 21 + ["B"] * 4 + ["C"] * 21

    result = _capacity("GOLD", secids)

    assert result.mature_raw_candidates == 2
    assert result.non_overlapping_observations == {5: 2, 10: 2, 20: 2}


def test_capacity_checks_entire_contract_window_not_only_endpoints() -> None:
    # At index 0 and index 20 the contract is A, but B appears in between.
    # A start/end-only test would incorrectly accept this forecast window.
    secids = ["A"] * 10 + ["B"] * 5 + ["A"] * 30
    result = _capacity("GOLD", secids)

    assert result.mature_raw_candidates == 2


def test_ambiguous_stored_session_fails_closed() -> None:
    history, store = _history("GOLD", ["GDA"] * 30)
    try:
        session_symbol = _verification_symbol("GOLD", "D1", is_root=True)
        ambiguous_date = (START + timedelta(days=5)).isoformat()
        store.record_sessions(session_symbol, "GDB", (ambiguous_date,))
        end = (START + timedelta(days=29)).isoformat()

        with pytest.raises(RuntimeError, match="exactly one contract"):
            stored_contract_capacity(
                history,
                "GOLD",
                from_date=START.isoformat(),
                till_date=end,
                step_sessions=5,
                max_points=80,
            )
    finally:
        store.close()
