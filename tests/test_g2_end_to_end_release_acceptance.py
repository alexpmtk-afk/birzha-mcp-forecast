"""Exact integrated G2 acceptance: source->Forecast->history->schedule->TSA->cold restore.

All test inputs are synthetic; TSA cryptographic responses are MOCKED here.
This proves inter-module contracts and cold restore, never real provider/clock
attestation or production readiness. No external requests.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import shutil

import pytest

from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from birzha.storage.g2_timestamp_evidence import (
    append_signed_time_evidence, read_signed_time_evidence,
)
from birzha.storage.g2_schedule_evidence import (
    append_official_schedule_evidence, read_official_schedule_evidence,
)
from birzha.application.g2_official_schedule_witness import (
    canonical, derived_candidate_from_staging,
)
from birzha.application.g2_tsa_policy_cutoff_review import (
    conditional_before_event_review,
)
from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
from scripts.g2_release_gate import validate
from test_g2_freeze_real_forecast_from_source_receipts import NOW, source_fixture
from test_g2_atomic_calendar_capture import calendar_witness
from test_g2_official_schedule_witness import witness
from test_g2_rfc3161_timestamp_retention import materials
from test_g2_tsa_policy_cutoff_review import synthetically_signed


GATE_PATH = Path(__file__).resolve().parents[1] / "docs/G2_RELEASE_GATE_20261010.json"


def _open(path: Path, root: Path):
    return SingleDuckDBProspectiveStagingJournal(
        path, staging_root=root, clock=lambda: NOW,
    )


def test_complete_synthetic_staging_evidence_survives_closed_cold_backup_restore(tmp_path):
    raw = tmp_path / "independent-synthetic-source"
    root = tmp_path / "original-disposable-store"
    root.mkdir()
    source_fixture(raw)

    calendar = calendar_witness()
    frozen = issue_atomic_staging(
        raw, root, "SBER", clock=lambda: NOW, calendar_witness=calendar,
    )
    assert frozen["status"] == "APPENDED"
    assert frozen["same_provider_history_witness_persisted"] is True
    fid = frozen["forecast_id"]
    file = root / "SBER.duckdb"
    original = _open(file, root)
    schedule = witness()
    schedule["forecast_id"] = fid
    schedule_bytes = canonical(schedule)
    files = materials(tmp_path)
    try:
        assert append_official_schedule_evidence(
            original, fid, schedule_bytes,
        )["status"] == "APPENDED"
        tsa = append_signed_time_evidence(
            original, fid, runner=synthetically_signed, **files,
        )
        assert tsa["status"] == "APPENDED"
        assert tsa["external_ca_independently_approved"] is False

        pre = original.audit()
        assert pre["captures"] == 1
        assert pre["outcomes"] == 0
        assert original.read_original_calendar_witness(fid)
        assert original.read_original_source_manifest(fid) == (
            raw / "manifest.json"
        ).read_bytes()

        reviewed_event = derived_candidate_from_staging(original, fid)
        assert reviewed_event["external_schedule_snapshot_sha256"] == sha256(
            schedule_bytes
        ).hexdigest()
        candidate = {
            "schema": "G2_RFC3161_CANDIDATE_TRUST_REVIEW_V1",
            "reviewed_ca_bundle_sha256": sha256(
                files["ca_path"].read_bytes()
            ).hexdigest(),
            "acceptable_tsa_policy_oids": ["1.2.3.4.5"],
            "max_tsa_accuracy_ms": 2000,
        }
        conditional = conditional_before_event_review(
            original, fid, trust_candidate=candidate,
            event_cutoff_candidate=reviewed_event,
            runner=synthetically_signed,
        )
        assert conditional["status"] == "CANDIDATE_CRYPTO_AND_TIME_ORDER_CONSISTENT"
        assert conditional["production_authorized"] is False
        assert conditional["exchange_session_cutoff_independently_certified"] is False
        assert conditional["independent_tsa_authority_approved"] is False
        assert read_signed_time_evidence(original, fid)["signed_response"] == (
            files["response_path"].read_bytes()
        )
    finally:
        original.close()

    # A COLD backup is taken only after all writers have closed. Never copy a
    # hot DuckDB by hand: it could omit noncheckpointed WAL contents.
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    backup_file = backup_dir / "SBER.duckdb"
    shutil.copy2(file, backup_file)
    assert sha256(backup_file.read_bytes()).digest() == sha256(
        file.read_bytes()
    ).digest()

    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    restored_file = restored_dir / "SBER.duckdb"
    shutil.copy2(backup_file, restored_file)
    assert sha256(restored_file.read_bytes()).digest() == sha256(
        backup_file.read_bytes()
    ).digest()

    # Actual second DuckDB physical file, not the still-open primary handle.
    recovered = _open(restored_file, restored_dir)
    try:
        assert recovered.audit()["captures"] == 1
        assert recovered.audit()["outcomes"] == 0
        assert recovered.read_original_calendar_witness(fid)
        assert read_official_schedule_evidence(recovered, fid) == schedule_bytes
        kept = read_signed_time_evidence(recovered, fid)
        assert kept["original_query"] == files["query_path"].read_bytes()
        assert kept["signed_response"] == files["response_path"].read_bytes()
        assert kept["verification"]["external_ca_independently_approved"] is False
        assert kept["verification"]["forecast_t0_independently_measured"] is False
        assert kept["verification"]["before_future_outcome_cutoff_certified"] is False
        assert derived_candidate_from_staging(recovered, fid) == reviewed_event
        assert recovered.db.execute(
            "SELECT COUNT(*) FROM forecast_records"
        ).fetchone()[0] == 1
        assert recovered.db.execute(
            "SELECT COUNT(*) FROM outcome_records"
        ).fetchone()[0] == 0
    finally:
        recovered.close()

    # Restored archive cannot silently authorize a user-unapproved release.
    gate = validate(json.loads(GATE_PATH.read_text(encoding="utf8")))
    assert gate["release_allowed"] is False
    assert len(gate["blocking_requirements"]) == 12
    assert not any(
        p.name.endswith(".duckdb") for p in raw.iterdir()
    )


def test_e2e_original_history_disagreement_never_persists_any_forecast(tmp_path):
    raw = tmp_path / "independent-synthetic-source"
    root = tmp_path / "staging"
    root.mkdir()
    source_fixture(raw)
    wrong = calendar_witness()
    wrong["secid"] = "GAZP"
    with pytest.raises(Exception, match="calendar|SECID|corroboration"):
        issue_atomic_staging(raw, root, "SBER", clock=lambda: NOW,
                             calendar_witness=wrong)
    assert not list(root.iterdir())
