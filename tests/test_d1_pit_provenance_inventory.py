"""Provenance inventory tests, all synthetic and read-only at audit time."""
import hashlib
import json
import duckdb
import pytest
from scripts.d1_pit_provenance_inventory import audit


def fixture_archive(path, *, modern=False):
    db = duckdb.connect(str(path))
    extension = (
        ", available_at VARCHAR, available_at_confidence VARCHAR,"
        " observed_at VARCHAR, revision VARCHAR"
    ) if modern else ""
    db.execute(
        "CREATE TABLE historical_candles (secid VARCHAR, root_symbol VARCHAR,"
        " asset_class VARCHAR, timeframe VARCHAR, begin VARCHAR,"
        " end_time VARCHAR, open DOUBLE, close DOUBLE, high DOUBLE,"
        " low DOUBLE, completed BOOLEAN, source VARCHAR" + extension + ")"
    )
    db.execute(
        "INSERT INTO historical_candles "
        "(secid,root_symbol,asset_class,timeframe,begin,end_time,"
        "open,close,high,low,completed,source) VALUES "
        "('BRM5','BR','future','D1','2025-04-03','2025-04-03',1,2,2,1,true,'MOEX_ISS'),"
        "('GDH5','GOLD','future','D1','2025-04-03','2025-04-03',1,2,2,1,true,'MOEX_ISS'),"
        "('GOLD','GOLD','stock','D1','2025-04-03','2025-04-03',1,2,2,1,true,'MOEX_ISS')"
    )
    if modern:
        db.execute("CREATE TABLE historical_candle_revisions (secid VARCHAR)")
    db.close()


def test_legacy_archive_never_proves_historical_receipt(tmp_path):
    p = tmp_path / "archive.duckdb"
    fixture_archive(p)
    prior = hashlib.sha256(p.read_bytes()).hexdigest()
    result = audit(p)
    assert result["source_sha256_before"] == prior
    assert result["source_sha256_after"] == prior
    assert result["database_opened_read_only"]
    assert result["schema_evidence"]["strict_historical_T0"] == "NOT_PROVEN"
    assert result["schema_evidence"]["missing_provenance_columns"] == [
        "available_at", "available_at_confidence", "observed_at", "revision"
    ]
    assert result["schema_evidence"]["d1_futures_archive"]["GOLD"]["archive_bar_rows"] == 1
    assert hashlib.sha256(p.read_bytes()).hexdigest() == prior


def test_modern_schema_does_not_imply_vintage_proof(tmp_path):
    p = tmp_path / "modern.duckdb"
    fixture_archive(p, modern=True)
    result = audit(p)
    assert result["schema_evidence"]["missing_provenance_columns"] == []
    assert result["schema_evidence"]["has_revisions_table"]
    assert result["schema_evidence"]["strict_historical_T0"] == "NOT_PROVEN"
    assert result["schema_evidence"]["reason"] == "SCHEMA_IS_NOT_PROOF_OF_AS_KNOWN_AT_T0"


def test_v2_mismatched_source_fails_closed(tmp_path):
    p = tmp_path / "archive.duckdb"
    fixture_archive(p)
    j = tmp_path / "v2.json"
    j.write_text(json.dumps({
        "report_schema_version": "D1_RECONSTRUCTED_WARMUP_AUDIT_V2",
        "calendar_origin": "RECONSTRUCTED_MOEX_CURRENT_QUERY",
        "strict_historical_as_known_at_T0": False,
        "original_unchanged": True,
        "source_sha256_before": "incorrect",
        "source_sha256_after": "incorrect",
    }))
    with pytest.raises(ValueError, match="SHA mismatch"):
        audit(p, j)
