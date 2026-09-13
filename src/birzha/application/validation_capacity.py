"""Pre-performance sample-capacity checks for governed historical validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.market_data import is_futures_root_symbol


DEFAULT_HORIZONS = (5, 10, 20)


@dataclass(frozen=True, slots=True)
class ContractAwareCapacity:
    sessions: int
    mature_raw_candidates: int
    non_overlapping_observations: dict[int, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "sessions": self.sessions,
            "mature_raw_candidates": self.mature_raw_candidates,
            "non_overlapping_observations": {
                str(horizon): count
                for horizon, count in self.non_overlapping_observations.items()
            },
        }


def stored_contract_capacity(
    history: HistoricalDataService,
    symbol: str,
    *,
    from_date: str,
    till_date: str,
    step_sessions: int,
    max_points: int,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> ContractAwareCapacity:
    """Count samples that mature on one exact contract and do not overlap.

    This check uses only the already-verified D1 session calendar. It does not
    inspect prices or forecast outcomes, so it is safe to run before development
    and before the governed holdout is opened.

    The walk-forward validator requires every forecast horizon to mature on the
    exact same instrument as T0. A raw candidate is therefore retained only when
    the stored active SECID is unchanged from T0 through the longest horizon.
    Non-overlap is then counted from the *actual retained T0 indexes*. This is
    important for rolling futures: rejected candidates around a roll already
    create extra spacing and must not be thinned a second time by a blind
    every-N rule.
    """
    if step_sessions <= 0:
        raise ValueError("step_sessions must be > 0")
    if max_points <= 0:
        raise ValueError("max_points must be > 0")
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("horizons must contain positive session counts")

    sessions = history.session_dates(
        symbol,
        from_date=from_date,
        till_date=till_date,
    )
    if not sessions:
        return ContractAwareCapacity(
            sessions=0,
            mature_raw_candidates=0,
            non_overlapping_observations={horizon: 0 for horizon in horizons},
        )

    session_symbol = _session_symbol(history, symbol)
    contract_by_date = _contract_map(
        history,
        session_symbol=session_symbol,
        sessions=sessions,
        from_date=from_date,
        till_date=till_date,
    )

    longest = max(horizons)
    mature_indexes: list[int] = []
    stop = max(0, len(sessions) - longest)
    for index in range(0, stop, step_sessions):
        window = sessions[index : index + longest + 1]
        secids = {contract_by_date[item] for item in window}
        if len(secids) != 1:
            continue
        mature_indexes.append(index)
        if len(mature_indexes) >= max_points:
            break

    observations = {
        horizon: _count_non_overlapping_indexes(mature_indexes, horizon)
        for horizon in horizons
    }
    return ContractAwareCapacity(
        sessions=len(sessions),
        mature_raw_candidates=len(mature_indexes),
        non_overlapping_observations=observations,
    )


def _count_non_overlapping_indexes(indexes: list[int], horizon: int) -> int:
    """Greedily retain every real window that starts after the prior one ends."""
    selected = 0
    previous_end: int | None = None
    for index in indexes:
        if previous_end is not None and index < previous_end:
            continue
        selected += 1
        previous_end = index + horizon
    return selected


def _session_symbol(history: HistoricalDataService, symbol: str) -> str:
    resolver_known = hasattr(history.market_data, "direct_resolver")
    if not resolver_known:
        return symbol
    return _verification_symbol(
        symbol,
        "D1",
        is_root=is_futures_root_symbol(symbol),
    )


def _contract_map(
    history: HistoricalDataService,
    *,
    session_symbol: str,
    sessions: tuple[date, ...],
    from_date: str,
    till_date: str,
) -> dict[date, str]:
    bulk = getattr(history.store, "stored_session_contracts", None)
    rows = (
        bulk(session_symbol, from_date, till_date)
        if callable(bulk)
        else tuple(
            (item.isoformat(), secid)
            for item in sessions
            for secid in history.store.stored_session_secids(
                session_symbol, item.isoformat()
            )
        )
    )

    grouped: dict[date, set[str]] = {}
    for raw_date, secid in rows:
        trade_date = date.fromisoformat(str(raw_date)[:10])
        grouped.setdefault(trade_date, set()).add(str(secid))

    result: dict[date, str] = {}
    for trade_date in sessions:
        secids = grouped.get(trade_date, set())
        if len(secids) != 1:
            raise RuntimeError(
                "stored session must map to exactly one contract: "
                f"{session_symbol} {trade_date.isoformat()} found={sorted(secids)}"
            )
        result[trade_date] = next(iter(secids))
    return result
