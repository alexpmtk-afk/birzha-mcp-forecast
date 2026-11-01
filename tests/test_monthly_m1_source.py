"""Offline acceptance for canonical monthly M1 OHLCV; no provider/Drive/HOME IO."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import zipfile

import pytest
from birzha.storage.monthly_m1_source import MonthlyM1Error, publish_month


def source(path: Path, *, secid="RIH6", symbol="RI", records=None,
           wrong_hash=False, marker="first"):
    if records is None:
        records = [
            dict(begin="2026-01-05 10:00:00", end="2026-01-05 10:00:59",
                 open=101, high=103, low=100, close=102, volume=7, value=0)
        ]
    doc = dict(symbol=symbol, exact_secid=secid, source="OFFICIAL_MOEX_ISS",
               raw={"M1": records}, range=["2026-01-01", "2026-01-31"])
    body = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()
    manifest = dict(secid=secid, archive_sha256=("x" * 64 if wrong_hash else digest),
                    capture_run_id=marker)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("source/" + marker + "_source_and_derived.json", body)
        z.writestr("source/manifest.json", json.dumps(manifest))
    return path


def second_minute(volume=11):
    return dict(begin="2026-01-05 10:01:00", end="2026-01-05 10:01:59",
                open=102, high=105, low=101, close=104, volume=volume, value=0)


def publish(tmp_path, paths, **overrides):
    return publish_month(
        paths, tmp_path / "canonical", tmp_path / "recovery",
        symbol=overrides.get("symbol", "RI"), secid=overrides.get("secid", "RIH6"),
        month=overrides.get("month", "2026-01"),
        volume_required=overrides.get("volume_required", True)
    )


def rows_from_result(result):
    with zipfile.ZipFile(result.path) as z:
        doc = json.loads(z.read("source_and_derived.json"))
        manifest = json.loads(z.read("manifest.json"))
    return doc["raw"]["M1"], doc, manifest


def test_publishes_exact_ohlcv_volume_and_readback_and_noop(tmp_path):
    a = source(tmp_path / "first.zip")
    first = publish(tmp_path, [a])
    assert first.status == "PUBLISHED_READBACK_PASS"
    assert first.m1_rows == 1 and first.volume_total == 7
    assert first.zip_sha256 == hashlib.sha256(Path(first.path).read_bytes()).hexdigest()
    rows, doc, manifest = rows_from_result(first)
    assert len(rows) == 1 and rows[0]["volume"] == 7
    assert rows[0]["open"] == 101 and rows[0]["high"] == 103
    assert rows[0]["begin"] == "2026-01-05T10:00:00+03:00"
    assert manifest["payload_sha256"] == first.payload_sha256
    assert "zip_sha256" not in manifest  # outside ZIP only; no self-hash
    again = publish(tmp_path, [a])
    assert again.status == "NOOP_VERIFIED"
    assert again.zip_sha256 == first.zip_sha256
    assert list((tmp_path / "recovery").iterdir()) == []
    assert len(list((tmp_path / "canonical").glob("*.zip"))) == 1


def test_incremental_month_adds_only_new_minute_and_retains_old_version(tmp_path):
    a = source(tmp_path / "a.zip")
    b = source(tmp_path / "b.zip", records=[second_minute()])
    first = publish(tmp_path, [a])
    updated = publish(tmp_path, [a, b])
    assert updated.status == "PUBLISHED_READBACK_PASS"
    assert updated.m1_rows == 2 and updated.volume_total == 18
    assert updated.previous_revision and updated.previous_revision.endswith(".zip")
    recovery = tmp_path / "recovery" / updated.previous_revision
    assert recovery.exists()
    assert hashlib.sha256(recovery.read_bytes()).hexdigest() == first.zip_sha256
    rows, _, _ = rows_from_result(updated)
    assert [r["volume"] for r in rows] == [7, 11]
    assert publish(tmp_path, [b, a]).status == "NOOP_VERIFIED"
    assert len(list((tmp_path / "canonical").glob("*.zip"))) == 1
    assert len(list((tmp_path / "recovery").glob("*.zip"))) == 1


def test_recap_same_candles_but_different_capture_is_noop(tmp_path):
    a = source(tmp_path / "a.zip", marker="run1")
    b = source(tmp_path / "b.zip", marker="run2")
    first = publish(tmp_path, [a])
    again = publish(tmp_path, [a, b])
    assert again.status == "NOOP_VERIFIED"
    assert again.zip_sha256 == first.zip_sha256
    assert not list((tmp_path / "recovery").glob("*.zip"))


def test_conflicting_minute_blocks_publication_preserving_existing(tmp_path):
    a = source(tmp_path / "a.zip")
    result = publish(tmp_path, [a])
    old = Path(result.path).read_bytes()
    b = source(tmp_path / "b.zip", records=[
        dict(begin="2026-01-05 10:00:00", end="2026-01-05 10:00:59",
             open=101, high=103, low=100, close=102, volume=100, value=0)])
    with pytest.raises(MonthlyM1Error, match="conflicting"):
        publish(tmp_path, [a, b])
    assert Path(result.path).read_bytes() == old


def test_corrupt_manifest_blocks_without_creating_output(tmp_path):
    a = source(tmp_path / "bad.zip", wrong_hash=True)
    with pytest.raises(MonthlyM1Error, match="hash mismatch"):
        publish(tmp_path, [a])
    assert not list((tmp_path / "canonical").glob("*.zip"))


def test_missing_minutes_are_not_synthesized(tmp_path):
    a = source(tmp_path / "a.zip")
    gap = second_minute()
    gap["begin"] = "2026-01-05 10:14:00"
    gap["end"] = "2026-01-05 10:14:59"
    b = source(tmp_path / "b.zip", records=[gap])
    result = publish(tmp_path, [a, b])
    rows, _, _ = rows_from_result(result)
    assert len(rows) == 2 and result.volume_total == 18


def test_null_index_volume_preserved_but_not_allowed_for_future(tmp_path):
    empty_volume = dict(begin="2026-01-05 10:00:00", end="2026-01-05 10:00:59",
                        open=100, high=101, low=99, close=100, volume=None)
    a = source(tmp_path / "index.zip", symbol="IMOEX", secid="IMOEX", records=[empty_volume])
    with pytest.raises(MonthlyM1Error, match="volume"):
        publish(tmp_path, [a], symbol="IMOEX", secid="IMOEX", volume_required=True)
    result = publish(tmp_path, [a], symbol="IMOEX", secid="IMOEX", volume_required=False)
    rows, _, manifest = rows_from_result(result)
    assert rows[0]["volume"] is None and manifest["m1_volume_total"] is None


def test_wrong_contract_bad_ohlc_and_unsafe_symbol_fail_closed(tmp_path):
    a = source(tmp_path / "a.zip")
    with pytest.raises(MonthlyM1Error, match="SECID"):
        publish(tmp_path, [a], secid="RIM6")
    with pytest.raises(MonthlyM1Error, match="unsafe"):
        publish(tmp_path, [a], symbol="../RI")
    wrong = dict(begin="2026-01-05 10:00:00", end="2026-01-05 10:00:59",
                 open=101, high=102, low=103, close=101, volume=-5)
    b = source(tmp_path / "wrong.zip", records=[wrong])
    with pytest.raises(MonthlyM1Error, match="OHLC"):
        publish(tmp_path, [b])


def test_corrupted_existing_output_and_lock_fail_closed(tmp_path):
    a = source(tmp_path / "a.zip")
    first = publish(tmp_path, [a])
    Path(first.path).write_bytes(b"not a ZIP")
    with pytest.raises(MonthlyM1Error, match="ZIP"):
        publish(tmp_path, [a])
    Path(first.path).unlink()
    lock = (tmp_path / "canonical" / "RI_RIH6_2026-01_SOURCE_M1_OHLCV.zip.lock")
    lock.write_text("in-progress")
    with pytest.raises(MonthlyM1Error, match="busy"):
        publish(tmp_path, [a])
    assert lock.exists()
