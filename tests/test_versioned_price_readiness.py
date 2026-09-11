from birzha.application.historical_data import (
    D1_SESSION_VERIFICATION_VERSION,
    H1_FULL_VERIFICATION_VERSION,
    HistoricalDataService,
)
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


class _ProductionLikeMarket:
    direct_resolver = object()


def test_legacy_direct_price_markers_do_not_prove_m23_readiness() -> None:
    store = DuckDBHistoricalCandleStore()
    store.mark_verified("SBER", "D1", "2025-01-01", "2026-05-31")
    store.mark_verified("SBER", "H1", "2025-01-01", "2026-05-31")
    store.mark_session_range_verified("SBER", "2025-01-01", "2026-05-31")
    history = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_ProductionLikeMarket(), store=store
    )

    assert history.is_range_verified(
        "SBER", timeframe="D1", from_date="2025-01-01", till_date="2026-05-31"
    ) is False
    assert history.is_range_verified(
        "SBER", timeframe="H1", from_date="2025-01-01", till_date="2026-05-31"
    ) is False
    store.close()


def test_current_d1_h1_markers_prove_readiness_after_current_audit() -> None:
    store = DuckDBHistoricalCandleStore()
    d1_key = f"SBER#{D1_SESSION_VERIFICATION_VERSION}"
    h1_key = f"SBER#{H1_FULL_VERIFICATION_VERSION}"
    store.mark_verified(d1_key, "D1", "2025-01-01", "2026-05-31")
    store.mark_verified(h1_key, "H1", "2025-01-01", "2026-05-31")
    store.mark_session_range_verified("SBER", "2025-01-01", "2026-05-31")
    history = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_ProductionLikeMarket(), store=store
    )

    assert history.is_range_verified(
        "SBER", timeframe="D1", from_date="2025-01-01", till_date="2026-05-31"
    ) is True
    assert history.is_range_verified(
        "SBER", timeframe="H1", from_date="2025-01-01", till_date="2026-05-31"
    ) is True
    store.close()
