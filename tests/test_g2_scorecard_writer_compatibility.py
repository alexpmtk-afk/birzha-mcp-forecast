"""Run against the pinned current writer in the isolated compatibility CI job.

All forecasts, clocks, calendars and future prices below are synthetic.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest

pytest.importorskip("birzha.storage.prospective_single_store")

from birzha.application.prospective_capture import CaptureEvidence, CompletedSessions
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from scripts.g2_prospective_baseline_scorecard import build_scorecard


@pytest.mark.parametrize("market,secid,direction", [
    ("SBER", "SBER", "UP"), ("Si", "SiZ6", "DOWN"), ("BR", "BRX6", "UP"),
    ("GOLD", "GDZ6", "NEUTRAL"), ("IMOEX", "IMOEX", "UP"), ("RTSI", "RTSI", "UP"),
])
def test_current_writer_forecast_and_outcome_are_readable_without_changes(
    tmp_path: Path, market: str, secid: str, direction: str
) -> None:
    ledger = tmp_path / "atomic.duckdb"
    clock = [datetime(2026, 10, 12, 10, 1, tzinfo=timezone.utc)]
    record = ForecastRecord(
        forecast_id=f"synthetic-scorecard-{market}", symbol=market, secid=secid,
        created_at_t0="2026-10-12T10:00:00Z", engine_version="SYNTHETIC_COMPATIBILITY_ONLY",
        direction=direction, signal_strength=.2, control="UNKNOWN", route="UNAVAILABLE",
        horizons=tuple(HorizonForecast(h, direction, .2, None, None) for h in (5, 10, 20)),
        reasons=("synthetic compatibility case",), warnings=("not authenticated source/calendar",),
        validation_status="UNVALIDATED_BASELINE", reference_price=100.,
        snapshot_id=f"synthetic-snapshot-{market}", snapshot_contract_version="MARKET_SNAPSHOT_V2",
    )
    store = SingleDuckDBProspectiveStagingJournal(ledger, staging_root=tmp_path, clock=lambda: clock[0])
    try:
        store.capture(record, CaptureEvidence(
            source_payload=b"synthetic captured provider bytes",
            source_observed_at="2026-10-12T09:59:00Z",
            latest_completed_event_end="2026-10-12T09:58:00Z",
        ))
    finally:
        store.close()
    pending = build_scorecard(ledger)
    assert all(row["outcomes_pending"] == 1 and row["candidate_accuracy"] is None for row in pending["groups"])

    # These dates are declared synthetic sessions, not an authenticated exchange calendar.
    dates = ("2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19")
    terminal = 97.65432109 if direction == "DOWN" else 102.34567891
    clock[0] = datetime(2026, 10, 19, 17, 1, tzinfo=timezone.utc)
    store = SingleDuckDBProspectiveStagingJournal(ledger, staging_root=tmp_path, clock=lambda: clock[0])
    try:
        store.observe(record.forecast_id, 5, CompletedSessions(
            source_payload=b"synthetic future provider bytes",
            source_observed_at="2026-10-19T17:00:00Z", market=market, secid=secid,
            session_dates=dates, expected_calendar_dates=dates,
            candle_completed_at=tuple(f"{day}T16:00:00Z" for day in dates),
            candle_close=(100., 101., 100.5, 101.5, terminal),
        ))
        assert store.audit()["outcomes"] == 1
    finally:
        store.close()
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    report = build_scorecard(ledger)
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before
    assert report["read_only"] is True
    five = next(row for row in report["groups"] if row["horizon_sessions"] == 5)
    assert (five["market"], five["secid"]) == (market, secid)
    assert five["outcomes_completed"] == 1 and five["outcomes_pending"] == 0
    if direction == "NEUTRAL":
        assert five["neutral_forecasts"] == 1
        assert five["directional_forecasts_scored"] == 0
        assert five["candidate_accuracy"] is None
    else:
        assert five["directional_forecasts_scored"] == 1
        assert five["candidate_accuracy"] == 1.
        assert five["always_up_accuracy_same_rows"] == (0. if direction == "DOWN" else 1.)
    assert all(row["outcomes_pending"] == 1 and row["candidate_accuracy"] is None
               for row in report["groups"] if row["horizon_sessions"] in (10, 20))
