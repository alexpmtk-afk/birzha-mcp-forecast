from datetime import date, datetime, timezone

import pytest

from birzha.application.warmup_session_evidence import (
    WarmupSessionEvidenceOrigin,
    read_verified_warmup_session_dates,
    warmup_session_evidence_key,
    warmup_session_generation_id,
)
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


DATES = (date(2025, 1, 8), date(2025, 1, 9))


def test_generation_fingerprint_is_canonical_and_content_addressed() -> None:
    forward = warmup_session_generation_id(
        secid="GDM5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    reversed_order = warmup_session_generation_id(
        secid="GDM5",
        expected_dates=tuple(reversed(DATES)),
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    assert forward == reversed_order
    assert len(forward) == 64
    assert warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    ).endswith(forward)


def test_changed_contract_dates_or_origin_create_distinct_keys() -> None:
    captured = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    reconstructed = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.RECONSTRUCTED_MOEX,
    )
    changed_dates = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=(date(2025, 1, 7), *DATES),
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    changed_secid = warmup_session_evidence_key(
        secid="SiH5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    assert len({captured, reconstructed, changed_dates, changed_secid}) == 4
    assert "#CAPTURED_AT_SYNC#" in captured
    assert "#RECONSTRUCTED_MOEX#" in reconstructed


@pytest.mark.parametrize(
    "dates",
    [(), (date(2025, 1, 8), date(2025, 1, 8)), (datetime(2025, 1, 8, tzinfo=timezone.utc),)],
)
def test_generation_rejects_empty_duplicate_or_datetime_dates(dates) -> None:
    with pytest.raises(ValueError):
        warmup_session_evidence_key(
            secid="GDM5",
            expected_dates=dates,
            origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
        )


def test_exact_generation_reader_requires_verified_range_and_matching_secid() -> None:
    store = DuckDBHistoricalCandleStore()
    key = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    store.record_sessions(key, "GDM5", tuple(item.isoformat() for item in DATES))
    with pytest.raises(RuntimeError, match="not verified"):
        read_verified_warmup_session_dates(
            store,
            evidence_key=key,
            secid="GDM5",
            from_date="2025-01-08",
            till_date="2025-01-09",
        )

    store.mark_session_range_verified(key, "2025-01-08", "2025-01-09")
    assert read_verified_warmup_session_dates(
        store,
        evidence_key=key,
        secid="GDM5",
        from_date="2025-01-08",
        till_date="2025-01-09",
    ) == DATES
    with pytest.raises(RuntimeError, match="fingerprint"):
        read_verified_warmup_session_dates(
            store,
            evidence_key=key,
            secid="GDM5",
            from_date="2025-01-09",
            till_date="2025-01-09",
        )
    store.close()


def test_exact_generation_reader_rejects_dates_that_do_not_match_fingerprint() -> None:
    store = DuckDBHistoricalCandleStore()
    key = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    store.record_sessions(key, "GDM5", ("2025-01-08", "2025-01-10"))
    store.mark_session_range_verified(key, "2025-01-08", "2025-01-10")
    with pytest.raises(RuntimeError, match="fingerprint"):
        read_verified_warmup_session_dates(
            store,
            evidence_key=key,
            secid="GDM5",
            from_date="2025-01-08",
            till_date="2025-01-10",
        )
    store.close()


def test_exact_generation_reader_fails_closed_on_mixed_secids() -> None:
    store = DuckDBHistoricalCandleStore()
    key = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=DATES,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    store.record_sessions(key, "GDM5", ("2025-01-08",))
    store.record_sessions(key, "GDU5", ("2025-01-09",))
    store.mark_session_range_verified(key, "2025-01-08", "2025-01-09")
    with pytest.raises(RuntimeError, match="different SECID"):
        read_verified_warmup_session_dates(
            store,
            evidence_key=key,
            secid="GDM5",
            from_date="2025-01-08",
            till_date="2025-01-09",
        )
    store.close()
