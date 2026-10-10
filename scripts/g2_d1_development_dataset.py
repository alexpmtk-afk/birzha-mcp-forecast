"""Read-only, deterministic six-market reconstructed D1 Development dataset.

Usage:
 python -m scripts.g2_d1_development_dataset --archive /path/history.duckdb \
   --warmup-v2 /path/d1_reconstructed_warmup_audit_20261008_v2.json \
   --output-dir /path/research-artifacts

This NEVER opens an archive read/write and NEVER loads the reserved 2023–24
holdout as model features. Output is reconstructed research, NOT strict PIT.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from birzha.application.d1_research_features import (
    D1_RESEARCH_FEATURE_VERSION, build_d1_research_features,
)
from birzha.application.warmup_session_evidence import warmup_d1_evidence_key
from birzha.domain.market import Candle, CandleSeries, Instrument

MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
FUTURES = frozenset(("Si", "BR", "GOLD"))
CALENDAR_SUFFIX = "ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V2_ACTIVITY"
DEVELOPMENT_START = "2021-01-01"
DEVELOPMENT_END = "2022-12-31"
EXTRACTOR_VERSION = "G2_SIX_MARKET_D1_DEVELOPMENT_DATASET_V1"
SOURCE_SCHEMA_MINIMUM = frozenset((
    "secid", "symbol", "root_symbol", "board", "engine", "market",
    "asset_class", "timeframe", "begin", "end_time", "open", "close",
    "high", "low", "value", "volume", "completed", "source",
))


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _root_key(market: str) -> str:
    return f"{market}#{CALENDAR_SUFFIX}"


def load_v2(path: Path, archive_hash: str) -> dict[str, dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if (
        report.get("report_schema_version") != "D1_RECONSTRUCTED_WARMUP_AUDIT_V2"
        or report.get("source_sha256_before") != archive_hash
        or report.get("source_sha256_after") != archive_hash
        or report.get("strict_historical_as_known_at_T0") is not False
        or report.get("original_unchanged") is not True
        or report.get("calendar_origin") != "RECONSTRUCTED_MOEX_CURRENT_QUERY"
    ):
        raise ValueError("V2 provenance does not match the exact immutable archive")
    if set(report.get("markets", {})) != set(FUTURES):
        raise ValueError("V2 must contain exactly three futures roots")
    result: dict[str, dict[str, Any]] = {}
    manifest: dict[str, Any] = {}
    for market, payload in report["markets"].items():
        if payload.get("status") != "RECONSTRUCTED_RESEARCH_ONLY":
            raise ValueError("V2 market is not reconstructed research-only")
        per_contract = payload.get("per_contract", {})
        manifest[market] = {}
        for secid, record in per_contract.items():
            dates = tuple(record.get("expected_dates", []))
            if (
                len(dates) != 20 or dates != tuple(sorted(set(dates)))
                or record.get("expected_count") != 20
                or record.get("origin") != "RECONSTRUCTED_MOEX"
                or any(day >= record["first_active_date"] for day in dates)
                or warmup_d1_evidence_key(secid, dates, origin="RECONSTRUCTED_MOEX")
                   != record.get("evidence_key")
            ):
                raise ValueError(f"invalid V2 evidence generation {market}/{secid}")
            manifest[market][secid] = {
                name: record[name]
                for name in ("first_active_date", "origin",
                             "evidence_key", "expected_dates")
            }
        result[market] = per_contract
    fingerprint = hashlib.sha256(_canonical(manifest).encode()).hexdigest()
    if fingerprint != report.get("evidence_manifest_sha256"):
        raise ValueError("V2 evidence manifest SHA mismatch")
    return result


def _validate_schema(db: Any) -> bool:
    tables = {r[0] for r in db.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema','pg_catalog')"
    ).fetchall()}
    if not {"historical_candles", "historical_sessions",
            "historical_session_verified_ranges"} <= tables:
        raise ValueError("source schema lacks original calendar/evidence tables")
    columns = {str(r[1]) for r in db.execute(
        "PRAGMA table_info('historical_candles')"
    ).fetchall()}
    if not SOURCE_SCHEMA_MINIMUM <= columns:
        raise ValueError("source candles schema incomplete")
    return "data_capabilities_json" in columns


def _active_calendar(db: Any, market: str) -> tuple[tuple[str, str], ...]:
    key = _root_key(market)
    rows = tuple((str(day), str(secid)) for day, secid in db.execute("""
        SELECT trade_date, secid FROM historical_sessions
        WHERE symbol=? ORDER BY trade_date, secid
    """, [key]).fetchall())
    if not rows:
        raise ValueError(f"{market}: no archived active-root calendar")
    dates = tuple(day for day, _ in rows)
    if dates != tuple(sorted(set(dates))):
        raise ValueError(f"{market}: duplicate/unsorted calendar")
    ranges = tuple((str(start), str(end)) for start, end in db.execute("""
        SELECT from_date, till_date FROM historical_session_verified_ranges
        WHERE symbol=? ORDER BY from_date
    """, [key]).fetchall())
    if not ranges or any(not any(start <= day <= end for start, end in ranges)
                         for day in dates):
        raise ValueError(f"{market}: unverified active-root dates")
    return rows


def _read_bars(
    db: Any, secid: str, lower: str, upper: str, has_caps: bool
) -> tuple[CandleSeries | None, tuple[str, ...]]:
    column = "data_capabilities_json" if has_caps else "NULL"
    rows = db.execute(f"""
        SELECT secid, symbol, root_symbol, board, engine, market, asset_class,
               begin, end_time, open, close, high, low, value, volume,
               completed, source, {column}
        FROM historical_candles
        WHERE timeframe='D1' AND secid=? AND begin >= ? AND begin < ?
        ORDER BY begin
    """, [secid, lower, date.fromisoformat(upper).isoformat()+"Z"]).fetchall()
    # upper is an ISO calendar day; compare by day, not by time-of-day.
    rows = [r for r in rows if lower <= str(r[7])[:10] <= upper]
    if not rows:
        return None, ()
    dates = tuple(str(r[7])[:10] for r in rows)
    record = rows[-1]
    try:
        capabilities = tuple(json.loads(record[17])) if record[17] else ()
    except (TypeError, ValueError):
        capabilities = ()
    inst = Instrument(
        symbol=str(record[1]), secid=str(record[0]),
        root_symbol=str(record[2]) if record[2] else None,
        board=str(record[3]), engine=str(record[4]),
        market=str(record[5]), asset_class=str(record[6]),
        data_capabilities=capabilities,
    )
    candles = tuple(Candle(
        open=r[9], close=r[10], high=r[11], low=r[12],
        value=r[13], volume=r[14], begin=str(r[7]),
        end=str(r[8]), completed=bool(r[15]), source=str(r[16]),
    ) for r in rows)
    return CandleSeries(inst, "D1", candles), dates


def make_dataset(
    db: Any, evidence: dict[str, dict[str, Any]],
    *, has_caps: bool, from_day: str = DEVELOPMENT_START,
    till_day: str = DEVELOPMENT_END,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    if from_day < DEVELOPMENT_START or till_day > DEVELOPMENT_END or from_day > till_day:
        raise ValueError("research window must remain inside exposed Development 2021-22")
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    totals: dict[str, Any] = {}
    for market in MARKETS:
        calendar = _active_calendar(db, market)
        seen_contract: list[tuple[str, str]] = []
        eligible = 0
        rejects = Counter()
        for session, secid in calendar:
            if session > till_day:
                break
            if seen_contract and seen_contract[-1][1] != secid:
                seen_contract.clear()  # Never stitch across a futures roll.
            seen_contract.append((session, secid))
            if session < from_day:
                continue
            if market == "GOLD" and not secid.upper().startswith("GD"):
                reason = "WRONG_GOLD_CONTRACT"
            else:
                active = tuple(day for day, sid in seen_contract if sid == secid)
                missing = max(0, 21-len(active))
                if missing:
                    # Equities/indices need a complete 21-bar active calendar.
                    # Futures may use the exact V2 reconstructed preactive
                    # sessions ONLY if this is a first observed active segment.
                    record = evidence.get(market, {}).get(secid) if market in FUTURES else None
                    if (record is None or record.get("first_active_date") != active[0]
                        or len(record.get("expected_dates", ())) < missing):
                        expected = ()
                        reason = "UNVERIFIED_PREACTIVE_EXACT_SESSIONS"
                    else:
                        expected = tuple(record["expected_dates"][-missing:]) + active
                        reason = ""
                        origin = "RECONSTRUCTED_MOEX"
                        key = str(record["evidence_key"])
                else:
                    expected = active[-21:]
                    origin = "ARCHIVED_ACTIVE_ROOT_CALENDAR"
                    key = _root_key(market)
                    reason = ""
                if not reason and len(expected) != 21:
                    reason = "INSUFFICIENT_EXACT_SESSIONS"
                if not reason:
                    source, observed_dates = _read_bars(
                        db, secid, expected[0], expected[-1], has_caps
                    )
                    if source is None:
                        reason = "NO_STORED_CANDLES"
                    elif observed_dates != expected:
                        reason = "UNVERIFIED_EXTRA_OR_MISSING_D1_SESSION"
                    elif (
                        (market == "GOLD" and source.instrument.asset_class != "future")
                        or (market in ("SBER", "Si", "BR") and market != "SBER"
                            and source.instrument.asset_class != "future")
                        or (market == "SBER" and source.instrument.asset_class != "equity")
                        or (market in ("IMOEX", "RTSI") and source.instrument.asset_class != "index")
                    ):
                        reason = "INSTRUMENT_IDENTITY_MISMATCH"
                    else:
                        try:
                            feature_set = build_d1_research_features(
                                source, exact_secid=secid,
                                expected_sessions=expected,
                                evidence_origin=origin, evidence_key=key,
                            ).to_dict()
                        except ValueError:
                            reason = "BAD_EXACT_SESSION_PAYLOAD"
                        else:
                            required = ("atr14_sma_tr", "d20_atr", "er20", "w20_atr")
                            if any(feature_set["features"][field]["status"] != "AVAILABLE"
                                   for field in required):
                                reason = "REQUIRED_D1_FEATURE_UNAVAILABLE"
                            else:
                                accepted.append({
                                    "market": market, "secid": secid,
                                    "session": session,
                                    "bar_event_end_at": source.candles[-1].end,
                                    "decision_knowledge_cutoff_at": None,
                                    "historical_first_receipt": "NOT_PROVEN",
                                    "calendar_evidence_origin": origin,
                                    "calendar_evidence_key": key,
                                    "expected_sessions": list(expected),
                                    "source": "HISTORICAL_ARCHIVE_RECONSTRUCTED",
                                    "features": feature_set,
                                })
                                eligible += 1
            if reason:
                excluded.append({
                    "market": market, "secid": secid,
                    "session": session, "reason": reason,
                })
                rejects[reason] += 1
        n = sum(1 for x in calendar if from_day <= x[0] <= till_day)
        totals[market] = {
            "development_active_candidates": n,
            "admitted_reconstructed": eligible,
            "excluded": sum(rejects.values()),
            "exclusion_reasons": dict(sorted(rejects.items())),
        }
        if eligible + sum(rejects.values()) != n:
            raise RuntimeError(f"{market}: incomplete candidate accounting")
    return accepted, excluded, totals


def run(archive: Path, v2: Path, output_dir: Path) -> dict[str, Any]:
    if not archive.is_file() or not v2.is_file():
        raise FileNotFoundError("archive and V2 evidence must exist")
    src_sha = _hash(archive)
    evidence = load_v2(v2, src_sha)
    import duckdb
    connection = duckdb.connect(str(archive), read_only=True)
    try:
        has_caps = _validate_schema(connection)
        accepted, excluded, totals = make_dataset(
            connection, evidence, has_caps=has_caps
        )
    finally:
        connection.close()
    if _hash(archive) != src_sha:
        raise RuntimeError("original archive changed during read-only extraction")
    # No arbitrary notebook state; byte content is canonical/deterministic.
    output = (
        "".join(_canonical(item)+"\n" for item in accepted).encode("utf-8"),
        "".join(_canonical(item)+"\n" for item in excluded).encode("utf-8"),
    )
    here = Path(__file__).resolve()
    formula = Path(build_d1_research_features.__code__.co_filename).resolve()
    manifest = {
        "schema": EXTRACTOR_VERSION, "scope": "RECONSTRUCTED_RESEARCH_ONLY",
        "strict_historical_as_known_at_T0": False,
        "markets": list(MARKETS),
        "development_from": DEVELOPMENT_START,
        "development_till": DEVELOPMENT_END,
        "source_sha256_before_after": src_sha,
        "v2_report_sha256": _hash(v2),
        "extractor_code_sha256": _hash(here),
        "feature_code_sha256": _hash(formula),
        "feature_version": D1_RESEARCH_FEATURE_VERSION,
        "accepted_rows_sha256": hashlib.sha256(output[0]).hexdigest(),
        "exclusion_rows_sha256": hashlib.sha256(output[1]).hexdigest(),
        "accepted_rows": len(accepted), "excluded_rows": len(excluded),
        "per_market": totals,
        "warnings": [
            "Original archive historical first-receipt/vintage NOT_PROVEN",
            "Active-root calendar historical causality NOT_PROVEN",
            "V2 preactive dates reconstructed from later public exchange queries",
            "Output never represents calibrated predictive skill or untouched OOS",
        ],
    }
    if _hash(archive) != src_sha:
        raise RuntimeError("source archive hash changed before output commit")
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in (
        ("g2_d1_development_rows.jsonl", output[0]),
        ("g2_d1_exclusions.jsonl", output[1]),
        ("g2_d1_manifest.json", (_canonical(manifest)+"\n").encode("utf-8")),
    ):
        target = output_dir / filename
        if target.exists():
            if target.read_bytes() != payload:
                raise FileExistsError(f"refusing to overwrite unlike artifact {target}")
            continue
        # Atomic replace is safe for output artifacts; archive stays read-only.
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(
            prefix="g2_", dir=output_dir, delete=False
        ) as f:
            temp = Path(f.name)
            f.write(payload)
        try:
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--warmup-v2", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(run(args.archive, args.warmup_v2, args.output_dir),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
