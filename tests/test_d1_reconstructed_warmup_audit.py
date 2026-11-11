"""Safe reconstructed warmup audit tests: no network and in-memory DuckDB."""

from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path

from birzha.application.d1_research_eligibility import (
    load_verified_d1_warmup_evidence,
)
from birzha.application.warmup_session_evidence import warmup_d1_evidence_key
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore
from scripts.d1_reconstructed_warmup_audit import (
    _reconstruct_one_warmup, _root_key, audit_copy,
)


class _Calendar:
    def __init__(self, *, omit=None):
        self.omit = omit

    def dates(self, *, from_date, till_date, **kwargs):
        active = date(2025, 2, 3)
        return tuple(
            day for day in (active - timedelta(days=d) for d in range(20, 0, -1))
            if from_date <= day <= till_date and day != self.omit
        )


def _sample_store(*, missing=None):
    store = DuckDBHistoricalCandleStore()
    contract = Instrument(
        symbol="BR", secid="BRM5", root_symbol="BR", asset_class="future",
        board="RFUD", engine="futures", market="forts",
    )
    active = date(2025, 2, 3)
    warmup = tuple(active-timedelta(days=n) for n in range(20, 0, -1))
    days = warmup + (active,)
    candles = tuple(
        Candle(open=90., close=100., high=101., low=89.,
               value=20., volume=10., completed=True,
               begin=day.isoformat() + " 10:00:00",
               end=day.isoformat() + " 23:59:59")
        for day in days if day != missing
    )
    store.upsert_series(CandleSeries(contract, "D1", candles))
    key = _root_key("BR")
    store.record_sessions(key, "BRM5", (active.isoformat(),))
    store.mark_session_range_verified(key, active.isoformat(), active.isoformat())
    return store, active, warmup


def test_reconstructed_d1_first_active_session_recovers_with_moex_evidence():
    store, active, warmup = _sample_store()
    result = audit_copy(store, _Calendar(), roots=("BR",))
    assert result["BR"]["candidate_sessions"] == 1
    assert result["BR"]["eligible"] == 1
    assert result["BR"]["excluded"] == 0
    key = warmup_d1_evidence_key(
        "BRM5", tuple(d.isoformat() for d in warmup),
        origin="RECONSTRUCTED_MOEX",
    )
    evidence = load_verified_d1_warmup_evidence(store, "BRM5", key)
    assert evidence is not None and evidence.origin == "RECONSTRUCTED_MOEX"
    proof = result["BR"]["per_contract"]["BRM5"]
    assert proof["status"] == "RECONSTRUCTED_MOEX"
    assert proof["origin"] == "RECONSTRUCTED_MOEX"
    assert proof["first_active_date"] == active.isoformat()
    assert proof["evidence_key"] == key
    assert proof["expected_count"] == 20
    assert proof["expected_dates"] == [day.isoformat() for day in warmup]
    assert warmup_d1_evidence_key(
        "BRM5", tuple(proof["expected_dates"]), origin=proof["origin"]
    ) == proof["evidence_key"]
    assert store.stored_sessions(_root_key("BR"), active.isoformat(), active.isoformat()) == (
        active.isoformat(),
    )
    assert not store.is_verified("BRM5#CONTRACT_WARMUP_V2_ACTIVITY",
                                 "D1", warmup[0].isoformat(), warmup[-1].isoformat())
    store.close()


def test_missing_internal_moex_session_cannot_be_hidden_by_older_bar():
    active = date(2025, 2, 3)
    store, _, _ = _sample_store(missing=active-timedelta(days=7))
    report = audit_copy(store, _Calendar(), roots=("BR",))["BR"]
    assert report["eligible"] == 0
    assert report["exclusion_reasons"]["UNVERIFIED_WARMUP"] == 1
    assert report["contract_warmup_status"]["MISSING_PREACTIVE_BAR"] == 1
    proof = report["per_contract"]["BRM5"]
    assert proof["first_active_date"] == active.isoformat()
    assert proof["evidence_key"] is None
    assert proof["origin"] is None
    assert proof["expected_dates"] == []
    assert proof["expected_count"] == 0
    store.close()


def test_current_moex_calendar_with_only_19_dates_is_not_certified():
    store, _, warmup = _sample_store()
    key, reason = _reconstruct_one_warmup(
        store, _Calendar(omit=warmup[7]), "BRM5", "2025-02-03"
    )
    assert key is None and reason == "INSUFFICIENT_MOEX_HISTORY"
    store.close()


def test_absent_verified_root_calendar_fails_closed():
    store, active, _ = _sample_store()
    store._connection.execute(
        "DELETE FROM historical_session_verified_ranges WHERE symbol=?",
        [_root_key("BR")],
    )
    result = audit_copy(store, _Calendar(), roots=("BR",))
    assert result["BR"]["status"] == "UNVERIFIED_ACTIVE_CALENDAR"
    store.close()

def test_disposable_run_exports_reproducible_manifest_and_preserves_archive(
    tmp_path, monkeypatch,
):
    import scripts.d1_reconstructed_warmup_audit as module

    archive = tmp_path / "original.duckdb"
    # Build an on-disk fixture, then reopen it only through the disposable
    # source-copy path inside run().
    fixture, active, warmup = _sample_store()
    # Re-create the same fixture as a file rather than extracting DB bytes from
    # an in-memory connection.
    instrument = fixture.stored_instrument("BRM5")
    assert instrument is not None
    candles = fixture.read(
        instrument, "D1", warmup[0].isoformat(), active.isoformat()
    )
    fixture.close()
    persisted = DuckDBHistoricalCandleStore(str(archive))
    persisted.upsert_series(candles)
    root_key = _root_key("BR")
    persisted.record_sessions(root_key, "BRM5", (active.isoformat(),))
    persisted.mark_session_range_verified(
        root_key, active.isoformat(), active.isoformat()
    )
    persisted.close()

    monkeypatch.setattr(module, "MoexIssClient", lambda: object())
    monkeypatch.setattr(module, "MoexTradingCalendar", lambda _provider: _Calendar())
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    result = module.run(archive, roots=("BR",))
    assert result["report_schema_version"] == module.AUDIT_REPORT_SCHEMA_VERSION
    assert datetime.fromisoformat(result["audit_started_at_utc"]).tzinfo is not None
    assert result["audit_script_sha256"] == hashlib.sha256(
        Path(module.__file__).read_bytes()
    ).hexdigest()
    assert result["source_sha256_before"] == before
    assert result["source_sha256_after"] == before
    assert result["original_unchanged"] is True
    assert result["strict_historical_as_known_at_T0"] is False
    proof = result["markets"]["BR"]["per_contract"]["BRM5"]
    manifest = {
        "BR": {
            "BRM5": {
                "first_active_date": proof["first_active_date"],
                "origin": proof["origin"],
                "evidence_key": proof["evidence_key"],
                "expected_dates": proof["expected_dates"],
            }
        }
    }
    expected_manifest_hash = hashlib.sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    assert result["evidence_manifest_sha256"] == expected_manifest_hash
    assert len(proof["expected_dates"]) == 20
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
