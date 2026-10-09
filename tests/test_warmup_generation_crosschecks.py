"""Unique hostile-input assertions from PR #127 on canonical PR #128 / #129 keys."""
from birzha.application.d1_research_eligibility import load_verified_d1_warmup_evidence
from birzha.application.warmup_session_evidence import warmup_d1_evidence_key
from birzha.storage.historical_store import DuckDBHistoricalCandleStore

DATES = ("2025-01-08", "2025-01-09")


def test_canonical_key_reorders_dates_but_separates_origins():
    captured = warmup_d1_evidence_key("GDM5", DATES)
    assert captured == warmup_d1_evidence_key("GDM5", tuple(reversed(DATES)))
    assert captured != warmup_d1_evidence_key("GDM5", DATES, origin="RECONSTRUCTED_MOEX")
    assert captured != warmup_d1_evidence_key("GDM5", DATES[:1])
    assert captured != warmup_d1_evidence_key("GDU5", DATES)


def test_loader_rejects_mutated_generation_dates():
    store = DuckDBHistoricalCandleStore()
    try:
        key = warmup_d1_evidence_key("GDM5", DATES)
        store.record_sessions(key, "GDM5", ("2025-01-08", "2025-01-10"))
        store.mark_session_range_verified(key, "2025-01-08", "2025-01-10")
        assert load_verified_d1_warmup_evidence(store, "GDM5", key) is None
    finally:
        store.close()


def test_loader_rejects_mixed_secid_generation():
    store = DuckDBHistoricalCandleStore()
    try:
        key = warmup_d1_evidence_key("GDM5", DATES)
        store.record_sessions(key, "GDM5", ("2025-01-08",))
        store.record_sessions(key, "GDU5", ("2025-01-09",))
        store.mark_session_range_verified(key, "2025-01-08", "2025-01-09")
        assert load_verified_d1_warmup_evidence(store, "GDM5", key) is None
    finally:
        store.close()


def test_loader_rejects_truncated_certified_generation():
    store = DuckDBHistoricalCandleStore()
    try:
        key = warmup_d1_evidence_key("GDM5", DATES)
        store.record_sessions(key, "GDM5", ("2025-01-08",))
        store.mark_session_range_verified(key, "2025-01-08", "2025-01-09")
        assert load_verified_d1_warmup_evidence(store, "GDM5", key) is None
    finally:
        store.close()
