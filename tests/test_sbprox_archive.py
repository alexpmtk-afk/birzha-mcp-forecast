from datetime import date

import pytest

from birzha.application.sbprox_archive import _load, _snapshot, build, inventory, stage
from birzha.application.sbprox_archive_reader import SBProXResearchReader, profile_stats
from birzha.providers.sbprox_ri import parse_line, sha256


def row(day="11022026", hhmm="0900", series="03-26", ask=7, bid=3):
    delta, total = ask - bid, ask + bid
    fields = [f"{day} {hhmm}00.000", f"{hhmm}59.999", series,
              "100", "100", "100", "100", str(total), str(delta), "0", str(ask), str(bid)]
    level = [100, total, delta, 0, 0, 0, 0, ask, bid, 0, 999]
    return (":".join(fields + ["0"] * 33) + "*" + ":".join(map(str, level)) + "\r\n").encode()


@pytest.fixture
def reference(tmp_path):
    p = tmp_path / "ref"
    p.mkdir()
    _snapshot(p / "contracts.jsonl", [["header"], ["RIH6", "2025-01-01", "2026-03-19"],
                                    ["RIM6", "2025-06-01", "2026-06-18"]])
    _snapshot(p / "sessions.jsonl", [
        ["header"],
        ["ALL", "2026-02-10", "MARKET_CALENDAR", "CLOSED", "", "", "", "CLOSED_BY_MOEX_SCHEDULE"],
        ["RIH6", "2026-02-11", "OBSERVED_EXACT_SECID", "REGULAR", "19:05",
         "2026-02-11", "2026-02-12", "SPLIT_TRADE_DATE_BY_PHASE_NOT_ISS_ATTESTED"],
        ["RIH6", "2026-02-12", "OBSERVED_EXACT_SECID", "REGULAR", "19:05",
         "2026-02-12", "2026-02-13", "SPLIT_TRADE_DATE_BY_PHASE_NOT_ISS_ATTESTED"],
    ])
    _snapshot(p / "pilot_h1.jsonl", [])
    return p


def test_parser_validates_levels_and_preserves_unproven_fields():
    day = date(2026, 2, 11)
    bar = parse_line(row(), day, 1)
    assert (bar.secid, bar.open, bar.delta, bar.levels[0].raw[-1]) == ("RIH6", 100, 4, "999")
    with pytest.raises(ValueError, match="inconsistent"):
        parse_line(row().replace(b":7:3:0:999", b":8:3:0:999"), day, 1)
    with pytest.raises(ValueError, match="unknown"):
        parse_line(row(series="04-26"), day, 1)
    with pytest.raises(ValueError, match="does not match"):
        parse_line(row(day="10022026"), day, 1)


def test_zero_byte_and_duplicate_statuses(tmp_path, reference):
    source = tmp_path / "src"
    source.mkdir()
    for day in (10, 12, 13):
        (source / f"2026-02-{day}.locchache").touch()
    (source / "2026-02-11.locchache").write_bytes(row() * 2)
    records, ready = inventory(source, tmp_path / "out", reference,
                               date(2026, 2, 10), date(2026, 2, 14))
    assert not ready
    assert [r["status"] for r in records] == [
        "NO_TRADING_CONFIRMED", "QUARANTINE", "TRADING_DAY_MISSING_DATA",
        "ZERO_BYTE_UNVERIFIED", "NO_FILE_UNVERIFIED",
    ]
    assert "duplicate minute key" in records[1]["reason"]


def test_generation_repeats_and_keeps_overlap_and_evening_separate(tmp_path, reference):
    source = tmp_path / "src"
    source.mkdir()
    raw = source / "2026-02-11.locchache"
    raw.write_bytes(row() + row(series="06-26") + row(hhmm="1905", ask=2, bid=8))
    out = tmp_path / "out"
    day = date(2026, 2, 11)
    args = (source, out, reference, day, day)
    root, state = build(*args)
    assert state == "CREATED"
    manifest = _load(root / "manifest.json")[0]
    assert manifest["checksums"]["RAW/2026-02-11.locchache"] == sha256(raw)
    assert build(*args) == (root, "UNCHANGED")
    reader = SBProXResearchReader(root)
    start, end = "2026-02-11T00:00:00+03:00", "2026-02-12T00:00:00+03:00"
    assert reader.coverage("RIH6", start, end)["minutes"] == 2
    assert reader.coverage("RIM6", start, end)["minutes"] == 1
    with pytest.raises(ValueError):
        reader.coverage("RI", start, end)
    feature = reader.features("RIH6", start, end, "2026-02-11T19:05:59+03:00")
    assert (feature["minutes"], feature["observed_delta"], feature["available_at"]) == (1, 4, "UNKNOWN")
    full = reader.features("RIH6", start, end, end)
    assert (full["minutes"], full["observed_delta"]) == (2, -2)
    import duckdb

    with duckdb.connect() as db:
        sessions = db.execute("SELECT trade_session_date, observed_cumulative_delta FROM read_parquet(?) "
                              "ORDER BY first_observed", [str(root / "DERIVED/FLOW_SESSION.parquet")]).fetchall()
    assert [(str(day), d) for day, d in sessions] == [("2026-02-11", 4), ("2026-02-12", -6)]
    (root / "RAW" / raw.name).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        build(*args)


def test_failed_file_never_commits_partial_checkpoint(tmp_path):
    raw = tmp_path / "2026-02-11.locchache"
    raw.write_bytes(row() + b"invalid\n")
    checkpoint = tmp_path / "checkpoints"
    checkpoint.mkdir()
    args = ("cal", {"RIH6": ["2025-01-01", "2026-03-19"]}, {})
    with pytest.raises(ValueError, match="line 2"):
        stage(raw, checkpoint, sha256(raw), *args)
    assert not list(checkpoint.iterdir())
    raw.write_bytes(row())
    ready, _ = stage(raw, checkpoint, sha256(raw), *args)
    assert stage(raw, checkpoint, sha256(raw), *args)[0] == ready


def test_profile_rule_is_deterministic_and_research_only():
    stats = profile_stats({120: 5, 100: 5, 110: 2, 130: 1})
    assert (stats["poc"], stats["val"], stats["vah"], stats["lvn"], stats["hvn"]) == (
        100, 100, 120, [110], [120]
    )


def test_nested_reference_zip_selects_the_data_member(tmp_path):
    import json
    from zipfile import ZipFile

    from birzha.application.sbprox_moex_comparison import load_zip

    path = tmp_path / "reference.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("month/manifest.json", "{}")
        archive.writestr("month/source_and_derived.json", json.dumps({"exact_secid": "RIH6"}))
    assert load_zip(path, "RIH6")["exact_secid"] == "RIH6"
    with pytest.raises(ValueError, match="SECID"):
        load_zip(path, "RIM6")


def test_profile_migration_rejects_future_or_overlapping_windows():
    reader = SBProXResearchReader.__new__(SBProXResearchReader)
    a, b, c = ("2026-02-11T09:00:00+03:00", "2026-02-11T10:00:00+03:00",
               "2026-02-11T11:00:00+03:00")
    with pytest.raises(ValueError, match="completed"):
        reader.profile_migration("RIH6", a, b, b, c, b)
    with pytest.raises(ValueError, match="non-overlapping"):
        reader.profile_migration("RIH6", a, b, a, c, c)
