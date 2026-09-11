from birzha.application.historical_data import (
    D1_SESSION_VERIFICATION_VERSION,
    H1_FULL_VERIFICATION_VERSION,
    HistoricalDataService,
    M15_FULL_VERIFICATION_VERSION,
    ROLLING_HISTORY_VERIFICATION_VERSION,
)
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


FROM = "2025-01-01"
TILL = "2026-05-31"


class _ProductionLikeMarket:
    direct_resolver = object()


def _root_key(symbol: str, version: str) -> str:
    return f"{symbol}#{ROLLING_HISTORY_VERIFICATION_VERSION}#{version}"


def test_legacy_direct_price_markers_do_not_prove_m23_readiness() -> None:
    store = DuckDBHistoricalCandleStore()
    store.mark_verified("SBER", "D1", FROM, TILL)
    store.mark_verified("SBER", "H1", FROM, TILL)
    store.mark_session_range_verified("SBER", FROM, TILL)
    history = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_ProductionLikeMarket(), store=store
    )

    assert history.is_range_verified(
        "SBER", timeframe="D1", from_date=FROM, till_date=TILL
    ) is False
    assert history.is_range_verified(
        "SBER", timeframe="H1", from_date=FROM, till_date=TILL
    ) is False
    store.close()


def test_current_direct_d1_h1_markers_prove_readiness_after_current_audit() -> None:
    store = DuckDBHistoricalCandleStore()
    d1_key = f"SBER#{D1_SESSION_VERIFICATION_VERSION}"
    h1_key = f"SBER#{H1_FULL_VERIFICATION_VERSION}"
    store.mark_verified(d1_key, "D1", FROM, TILL)
    store.mark_verified(h1_key, "H1", FROM, TILL)
    store.mark_session_range_verified(d1_key, FROM, TILL)
    history = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_ProductionLikeMarket(), store=store
    )

    assert history.is_range_verified(
        "SBER", timeframe="D1", from_date=FROM, till_date=TILL
    ) is True
    assert history.is_range_verified(
        "SBER", timeframe="H1", from_date=FROM, till_date=TILL
    ) is True
    store.close()


def test_rolling_root_requires_composed_routing_and_timeframe_generation() -> None:
    store = DuckDBHistoricalCandleStore()
    history = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_ProductionLikeMarket(), store=store
    )

    # Legacy and timeframe-only evidence must not validate GOLD after the
    # rolling-root routing correction.
    store.mark_verified("GOLD", "D1", FROM, TILL)
    store.mark_verified(
        f"GOLD#{D1_SESSION_VERIFICATION_VERSION}", "D1", FROM, TILL
    )
    store.mark_session_range_verified("GOLD", FROM, TILL)
    store.mark_session_range_verified(
        f"GOLD#{D1_SESSION_VERIFICATION_VERSION}", FROM, TILL
    )
    assert history.is_range_verified(
        "GOLD", timeframe="D1", from_date=FROM, till_date=TILL
    ) is False

    d1_key = _root_key("GOLD", D1_SESSION_VERIFICATION_VERSION)
    h1_key = _root_key("GOLD", H1_FULL_VERIFICATION_VERSION)
    m15_key = _root_key("GOLD", M15_FULL_VERIFICATION_VERSION)
    store.mark_verified(d1_key, "D1", FROM, TILL)
    store.mark_session_range_verified(d1_key, FROM, TILL)
    store.mark_verified(h1_key, "H1", FROM, TILL)
    store.mark_verified(m15_key, "M15", FROM, TILL)

    assert history.is_range_verified(
        "GOLD", timeframe="D1", from_date=FROM, till_date=TILL
    ) is True
    assert history.is_range_verified(
        "GOLD", timeframe="H1", from_date=FROM, till_date=TILL
    ) is True
    assert history.is_range_verified(
        "GOLD", timeframe="M15", from_date=FROM, till_date=TILL
    ) is True
    store.close()


def test_session_dates_ignore_legacy_gold_calendar_rows() -> None:
    store = DuckDBHistoricalCandleStore()
    d1_key = _root_key("GOLD", D1_SESSION_VERIFICATION_VERSION)

    # Simulate stale rows from the old exact-SECID GOLD routing.
    store.record_sessions("GOLD", "GOLD", ("2025-01-03", "2025-01-06"))
    store.mark_session_range_verified("GOLD", "2025-01-01", "2025-01-10")

    # Current routing records the real GD* sessions under the versioned key.
    store.record_sessions(d1_key, "GDH5", ("2025-01-07", "2025-01-08"))
    store.mark_session_range_verified(d1_key, "2025-01-01", "2025-01-10")
    history = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_ProductionLikeMarket(), store=store
    )

    assert tuple(day.isoformat() for day in history.session_dates(
        "GOLD", from_date="2025-01-01", till_date="2025-01-10"
    )) == ("2025-01-07", "2025-01-08")
    store.close()
