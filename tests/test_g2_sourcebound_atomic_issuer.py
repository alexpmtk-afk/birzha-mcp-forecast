"""Synthetic complete raw pages to one immutable DuckDB; never prove real MOEX."""
from datetime import timedelta
from hashlib import sha256
import json
import pytest

from birzha.application.prospective_capture import AdmissionRefused
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
from scripts.g2_capture_and_issue_atomic_staging import capture_and_issue_one
from test_g2_freeze_real_forecast_from_source_receipts import NOW, source_fixture


def stage(tmp_path):
    src = tmp_path / "raw"
    staging = tmp_path / "isolated_staging"
    source_fixture(src)
    staging.mkdir()
    return src, staging


def test_complete_fresh_source_binds_issue_time_and_one_atomic_canonical_file(tmp_path):
    src, root = stage(tmp_path)
    before = {p.name: sha256(p.read_bytes()).hexdigest() for p in src.iterdir()}
    result = issue_atomic_staging(src, root, "SBER", clock=lambda: NOW)
    assert result["status"] == "APPENDED"
    assert result["source_observed_at"] == "2026-10-10T15:00:05Z"
    assert result["issued_at_t0"] == "2026-10-10T15:00:20Z"
    assert result["strict_ex_ante_proof"] is False
    assert result["independent_provider_and_clock_attestation"] is False
    assert result["canonical_atomic_audit"]["captures"] == 1
    assert result["canonical_atomic_audit"]["outcomes"] == 0
    assert [p.name for p in root.iterdir()] == ["SBER.duckdb"]
    canon = DuckDBForecastJournal(str(root / "SBER.duckdb"))
    try:
        forecast = canon.get(result["forecast_id"])
        assert forecast.created_at_t0 == result["issued_at_t0"]
        assert forecast.secid == result["secid"] == "SBER"
        assert forecast.snapshot_id == result["snapshot_id"]
        assert canon.count() == 1
    finally:
        canon.close()
    assert before == {p.name: sha256(p.read_bytes()).hexdigest() for p in src.iterdir()}


def test_exact_retry_is_idempotent_but_different_issue_t0_cannot_reissue_same_bytes(tmp_path):
    src, root = stage(tmp_path)
    first = issue_atomic_staging(src, root, "SBER", clock=lambda: NOW)
    again = issue_atomic_staging(src, root, "SBER", clock=lambda: NOW)
    assert again["status"] == "DUPLICATE_IDENTICAL"
    assert again["forecast_id"] == first["forecast_id"]
    with pytest.raises(AdmissionRefused, match="cannot be reissued"):
        issue_atomic_staging(src, root, "SBER", clock=lambda: NOW + timedelta(seconds=10))
    journal = SingleDuckDBProspectiveStagingJournal(root / "SBER.duckdb", staging_root=root, clock=lambda: NOW)
    try:
        assert journal.audit()["captures"] == 1
    finally:
        journal.close()


@pytest.mark.parametrize("case", ["modified_body", "missing_page", "unlisted_page", "page_identity", "repeated_page", "wrong_requested_market"])
def test_bad_or_missing_raw_inventory_fails_before_any_forecast_write(tmp_path, case):
    src, root = stage(tmp_path)
    manifest_path = src / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    first = "SBER_SBER_D1_0.json"
    if case == "modified_body":
        (src / first).write_bytes(b"changed original pages")
    elif case == "missing_page":
        (src / first).unlink()
    elif case == "unlisted_page":
        (src / "extra.json").write_text("{}", encoding="utf8")
    elif case == "page_identity":
        manifest["instruments"][0]["timeframes"]["D1"]["pages"][0]["sha256"] = "0" * 64
    elif case == "repeated_page":
        manifest["instruments"][0]["timeframes"]["D1"]["pages"].append(
            dict(manifest["instruments"][0]["timeframes"]["D1"]["pages"][0])
        )
    elif case == "wrong_requested_market":
        manifest["requested_markets"] = ["OTHER"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf8")
    with pytest.raises((ValueError, KeyError, AdmissionRefused)):
        issue_atomic_staging(src, root, "SBER", clock=lambda: NOW)
    assert list(root.glob("*.duckdb")) == []


def test_source_staleness_rejected_before_opening_staging(tmp_path):
    src, root = stage(tmp_path)
    with pytest.raises(ValueError, match="immediately"):
        issue_atomic_staging(src, root, "SBER", clock=lambda: NOW + timedelta(minutes=6))
    assert list(root.glob("*.duckdb")) == []


def test_failure_between_receipt_and_forecast_rolls_back_atomically(tmp_path):
    src, root = stage(tmp_path)
    def fail(event):
        if event == "AFTER_RECEIPT":
            raise OSError("synthetic precommit fault")
    with pytest.raises(OSError, match="synthetic precommit fault"):
        issue_atomic_staging(src, root, "SBER", clock=lambda: NOW, fault_hook=fail)
    db = SingleDuckDBProspectiveStagingJournal(root / "SBER.duckdb", staging_root=root, clock=lambda: NOW)
    try:
        assert db.audit()["captures"] == 0
    finally:
        db.close()
    assert issue_atomic_staging(src, root, "SBER", clock=lambda: NOW)["status"] == "APPENDED"


def test_production_or_missing_staging_paths_refused(tmp_path):
    src, root = stage(tmp_path)
    with pytest.raises(AdmissionRefused, match="existing disposable"):
        issue_atomic_staging(src, root / "missing", "SBER", clock=lambda: NOW)
    forbidden = tmp_path / "MCP-HOME"
    forbidden.mkdir()
    with pytest.raises(AdmissionRefused, match="HOME or production"):
        issue_atomic_staging(src, forbidden, "SBER", clock=lambda: NOW)
    assert not list(forbidden.glob("*.duckdb"))


def test_bounded_network_capture_wraps_single_market_then_issues(monkeypatch, tmp_path):
    root = tmp_path / "isolate"
    root.mkdir()
    capture = tmp_path / "new_capture"
    calls = []
    def fake_capture(provider, market_data, out_dir, *, clock, markets):
        calls.append(tuple(markets))
        source_fixture(out_dir)
        return {"verified_complete_market_count": 1, "failed_markets": [], "complete_markets": ["SBER"]}
    monkeypatch.setattr("scripts.g2_capture_and_issue_atomic_staging.capture_all", fake_capture)
    result = capture_and_issue_one(object(), object(), "SBER", capture, root, clock=lambda: NOW)
    assert calls == [("SBER",)]
    assert result["status"] == "APPENDED"
    assert result["capture_mode"] == "BOUNDED_LIVE_MOEX_ISS_NETWORK_SINGLE_MARKET"
    assert result["production_authorized"] is False
    assert result["independent_provider_and_clock_attestation"] is False


def test_incomplete_network_capture_never_reaches_issuer(monkeypatch, tmp_path):
    root = tmp_path / "isolated"
    root.mkdir()
    def fake_capture(*args, **kwargs):
        return {"verified_complete_market_count": 0, "failed_markets": [{"market": "SBER"}], "complete_markets": []}
    def forbidden_issue(*args, **kwargs):
        raise AssertionError("source incomplete yet issuer invoked")
    monkeypatch.setattr("scripts.g2_capture_and_issue_atomic_staging.capture_all", fake_capture)
    monkeypatch.setattr("scripts.g2_capture_and_issue_atomic_staging.issue_atomic_staging", forbidden_issue)
    with pytest.raises(ValueError, match="capture failed"):
        capture_and_issue_one(object(), object(), "SBER", tmp_path / "fresh", root, clock=lambda: NOW)
    assert not list(root.iterdir())


def test_existing_mature_outcome_does_not_block_new_independent_source_issuance(tmp_path):
    """Per-market reusable staging must not be one-shot after its first Outcome."""
    from birzha.application.prospective_capture import CompletedSessions

    src, root = stage(tmp_path)
    first = issue_atomic_staging(src, root, "SBER", clock=lambda: NOW)
    dates = tuple(f"2026-10-{i:02d}" for i in range(11, 16))
    later = CompletedSessions(
        source_payload=b"separately observed synthetic future bars",
        source_observed_at="2026-10-15T20:01:00Z",
        market="SBER", secid="SBER", session_dates=dates,
        expected_calendar_dates=dates,
        candle_completed_at=tuple(f"{d}T20:00:00Z" for d in dates),
        candle_close=tuple(101. + i for i in range(5))
    )
    recovered = SingleDuckDBProspectiveStagingJournal(
        root / "SBER.duckdb", staging_root=root, clock=lambda: NOW + timedelta(days=20)
    )
    try:
        assert recovered.observe(first["forecast_id"], 5, later)["status"] == "APPENDED"
        assert recovered.audit()["outcomes"] == 1
    finally:
        recovered.close()

    # A later synthetic source acquisition includes a DIFFERENT raw page
    # and fresh declared observation time. It is not a real provider proof.
    manifest_file = src / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf8"))
    raw_path = src / "SBER_SBER_D1_0.json"
    raw = json.loads(raw_path.read_bytes())
    raw["candles"]["data"][0][1] += .01
    updated = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    raw_path.write_bytes(updated)
    new_sha = sha256(updated).hexdigest()
    for page in manifest["instruments"][0]["timeframes"]["D1"]["pages"]:
        page["sha256"] = new_sha
    for item in manifest["all_payload_files"]:
        if item["name"] == raw_path.name:
            item["sha256"] = new_sha
    for meta in manifest["instruments"][0]["timeframes"].values():
        for page in meta["pages"]:
            page["observed_start_utc"] = "2026-10-31T15:00:00Z"
            page["observed_end_utc"] = "2026-10-31T15:00:05Z"
    manifest_file.write_text(json.dumps(manifest), encoding="utf8")

    second = issue_atomic_staging(
        src, root, "SBER", clock=lambda: NOW + timedelta(days=21)
    )
    assert second["status"] == "APPENDED"
    assert second["forecast_id"] != first["forecast_id"]
    assert second["canonical_atomic_audit"] == {
        "schema": "G2_ATOMIC_SINGLE_DUCKDB_STAGING_V2",
        "captures": 2, "outcomes": 1, "staging_only": True
    }
    store = SingleDuckDBProspectiveStagingJournal(
        root / "SBER.duckdb", staging_root=root,
        clock=lambda: NOW + timedelta(days=21)
    )
    try:
        assert store.audit()["captures"] == 2
        assert store.audit()["outcomes"] == 1
    finally:
        store.close()


@pytest.mark.parametrize("location", ["raw_root", "staging_root", "raw_parent", "page"])
def test_symlinked_path_or_page_is_rejected_before_database_access(tmp_path, location):
    src, root = stage(tmp_path)
    if location == "raw_root":
        link = tmp_path / "linked_source"
        link.symlink_to(src, target_is_directory=True)
        src = link
    elif location == "staging_root":
        link = tmp_path / "linked_staging"
        link.symlink_to(root, target_is_directory=True)
        root = link
    elif location == "raw_parent":
        parent = tmp_path / "linked_parent"
        parent.symlink_to(src, target_is_directory=True)
        src = parent / "."
    else:
        filename = src / "SBER_SBER_D1_0.json"
        backup = src / "tmp-original-backup"
        filename.rename(backup)
        filename.symlink_to(backup)
        # Actual source index still names the symlink; backup is an extra
        # unlisted file. Both should fail without a new Forecast.
    with pytest.raises(AdmissionRefused, match="symlink|inventory"):
        issue_atomic_staging(src, root, "SBER", clock=lambda: NOW)
    assert list((tmp_path / "isolated_staging").glob("*.duckdb")) == []


def test_capture_wrapper_rejects_home_source_path_before_network_request(monkeypatch, tmp_path):
    capture_root = tmp_path / "MCP-HOME"
    capture_root.mkdir()
    stage_root = tmp_path / "isolated_staging"
    stage_root.mkdir()
    called = []
    def forbidden_capture(*args, **kwargs):
        called.append(True)
        raise AssertionError("network collector should never start")
    monkeypatch.setattr(
        "scripts.g2_capture_and_issue_atomic_staging.capture_all", forbidden_capture
    )
    with pytest.raises(AdmissionRefused, match="HOME"):
        capture_and_issue_one(
            object(), object(), "SBER",
            capture_root / "raw", stage_root, clock=lambda: NOW
        )
    assert not called
    assert list(stage_root.iterdir()) == []


def test_capture_wrapper_rejects_linked_output_before_network_request(monkeypatch, tmp_path):
    stage_root = tmp_path / "safe_staging"
    stage_root.mkdir()
    home = tmp_path / "some-other-directory"
    home.mkdir()
    linked = tmp_path / "capture_link"
    linked.symlink_to(home, target_is_directory=True)
    called = []
    def forbidden_capture(*args, **kwargs):
        called.append(True)
        raise AssertionError("network collector started despite linked output")
    monkeypatch.setattr(
        "scripts.g2_capture_and_issue_atomic_staging.capture_all", forbidden_capture
    )
    with pytest.raises(AdmissionRefused, match="symlink"):
        capture_and_issue_one(
            object(), object(), "SBER", linked, stage_root, clock=lambda: NOW
        )
    assert not called
    assert list(stage_root.iterdir()) == []
