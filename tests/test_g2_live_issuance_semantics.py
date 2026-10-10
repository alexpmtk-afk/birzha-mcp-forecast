"""Causal audit of ordinary snapshot event time vs first live receipt time.

This is a synthetic *negative contract* test, not a claim that live forecasts
were correctly issued by the default ForecastService. PR #146's separate
actual-source capture path already uses a post-observation clock T0.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from birzha.application.forecast import ForecastService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.prospective_capture import (
    CaptureEvidence,
    ProspectivePilotLedger,
    AdmissionRefused,
)
from birzha.domain.market import Candle, CandleSeries, Instrument


INSTRUMENT = Instrument(
    symbol="SBER", secid="SBER", board="TQBR", engine="stock",
    market="shares", asset_class="equity",
    data_capabilities=("CANDLES", "TRADING_CALENDAR"),
)

def bar(begin, end, close=100.0):
    return Candle(
        open=close, high=close + 1, low=close - 1, close=close,
        volume=10., value=1000., begin=begin, end=end,
        completed=True, source="SYNTHETIC_NOT_MOEX",
    )

class OfflineMarketData:
    def resolve(self, symbol: str, *, as_of: date | None = None):
        assert symbol == "SBER" and as_of == date(2026, 8, 28)
        return INSTRUMENT

    def candles_for_instrument(self, instrument, *, timeframe, **kwargs):
        assert instrument.secid == "SBER"
        if timeframe == "D1":
            rows = (bar("2026-08-27 10:00:00", "2026-08-27 23:49:59"),)
        elif timeframe == "H1":
            rows = (bar("2026-08-28 12:00:00", "2026-08-28 12:59:59"),)
        else:
            rows = (bar("2026-08-28 13:00:00", "2026-08-28 13:14:59"),)
        return CandleSeries(instrument, timeframe, rows)

@dataclass(frozen=True)
class ProvenanceFixtureForecast:
    t0: str
    def to_dict(self):
        return {
            "forecast_id": "fixture_live_t0_audit", "symbol": "SBER", "secid": "SBER",
            "created_at_t0": self.t0, "record_version": "FORECAST_RECORD_V1_PROTOCOL_08",
            "snapshot_id": "synthetic_receipt_timing_audit",
            "snapshot_contract_version": "MARKET_SNAPSHOT_V2",
            "engine_version": "AUDIT_ONLY",
            "reference_price": 100.,
            "horizons": [
                {"sessions": h, "direction": "NEUTRAL", "signal_strength": 0.,
                 "expected_move_pct": None, "adverse_move_pct": None}
                for h in (5, 10, 20)
            ],
        }


def original_receipt():
    return CaptureEvidence(
        source_payload=b"synthetic-only observed after completed M15 candle",
        source_observed_at="2026-08-28T11:00:00Z",  # 14:00 Moscow
        latest_completed_event_end="2026-08-28T10:14:59Z",
    )


def test_default_non_historical_snapshot_t0_is_event_end_not_first_receipt():
    snapshot = MarketSnapshotService(market_data=OfflineMarketData()).build(
        "SBER", as_of_date="2026-08-28",
    )
    record = ForecastService(
        snapshots=MarketSnapshotService(market_data=OfflineMarketData())
    ).build("SBER", as_of_date="2026-08-28")
    assert snapshot.as_of == "2026-08-28 13:14:59"
    assert record.created_at_t0 == snapshot.as_of
    # This normal/reconstructed forecast does not include an attested first
    # source receipt, and cannot by itself be called a prospective live T0.
    assert record.created_at_t0.find("+") < 0 and "Z" not in record.created_at_t0


def test_pilot_rejects_naive_or_backdated_t0_for_real_received_source(tmp_path):
    clock = lambda: datetime(2026, 8, 28, 11, 0, 30, tzinfo=timezone.utc)
    ledger = ProspectivePilotLedger(tmp_path/"timing_audit.sqlite3", clock=clock)
    try:
        with pytest.raises(AdmissionRefused):
            ledger.capture(
                ProvenanceFixtureForecast("2026-08-28 13:14:59"),
                original_receipt(),
            )
        with pytest.raises(AdmissionRefused,match="future data or unobserved source at forecast T0"):
            ledger.capture(
                ProvenanceFixtureForecast("2026-08-28T13:14:59+03:00"),
                original_receipt(),
            )
        assert ledger.audit()["captures"] == 0
    finally:
        ledger.close()


def test_pilot_accepts_post_receipt_t0_but_does_not_prove_source_authenticity(tmp_path):
    # Shape-only guard: a synthetic receipt passes chronology if T0 is issued
    # after it. Real market provenance still needs independently verified bytes.
    clock = lambda: datetime(2026, 8, 28, 11, 0, 30, tzinfo=timezone.utc)
    ledger = ProspectivePilotLedger(tmp_path/"timing_positive.sqlite3", clock=clock)
    try:
        result = ledger.capture(
            ProvenanceFixtureForecast("2026-08-28T11:00:10Z"),
            original_receipt(),
        )
        assert result["status"] == "APPENDED"
        assert ledger.audit()["captures"] == 1
        assert ledger.audit()["outcomes"] == 0
        assert result["receipt"]["strict_historical_pit"] is False
    finally:
        ledger.close()
