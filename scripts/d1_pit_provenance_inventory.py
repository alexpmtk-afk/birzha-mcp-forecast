"""Read-only historical D1 evidence inventory. Never attests strict historical T0.

python -m scripts.d1_pit_provenance_inventory --archive <duckdb> --v2 <json>

No network, writes, migrations, price corrections, classifier or holdout reads.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOTS = {"BR", "SI", "GOLD"}
BASE_COLUMNS = {"secid", "root_symbol", "asset_class", "timeframe",
                "begin", "end_time", "open", "close", "high", "low",
                "completed", "source"}
PROVENANCE_COLUMNS = {"available_at", "available_at_confidence",
                      "observed_at", "revision"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def summarize_schema(db: Any) -> dict[str, Any]:
    tables = {str(row[0]) for row in db.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema','pg_catalog')"
    ).fetchall()}
    if "historical_candles" not in tables:
        raise ValueError("historical_candles table missing")
    cols = {str(row[1]) for row in db.execute(
        "PRAGMA table_info('historical_candles')"
    ).fetchall()}
    if not BASE_COLUMNS <= cols:
        raise ValueError("required candle columns missing: "
                         + str(sorted(BASE_COLUMNS-cols)))
    rows = db.execute("""
        SELECT UPPER(root_symbol), COUNT(*), COUNT(DISTINCT secid),
               MIN(SUBSTR(begin, 1, 10)), MAX(SUBSTR(begin, 1, 10)),
               SUM(CASE WHEN completed IS NOT TRUE
                            OR open IS NULL OR close IS NULL
                            OR high IS NULL OR low IS NULL
                        THEN 1 ELSE 0 END)
          FROM historical_candles
         WHERE timeframe='D1' AND asset_class='future'
           AND UPPER(root_symbol) IN ('BR','SI','GOLD')
         GROUP BY UPPER(root_symbol) ORDER BY 1
    """).fetchall()
    return {
        "column_names": sorted(cols),
        "missing_provenance_columns": sorted(PROVENANCE_COLUMNS-cols),
        "has_revisions_table": "historical_candle_revisions" in tables,
        "d1_futures_archive": {
            str(root): {
                "archive_bar_rows": int(n), "contract_count": int(c),
                "first_day": str(first), "last_day": str(last),
                "invalid_or_incomplete_rows": int(invalid or 0),
            } for root, n, c, first, last, invalid in rows
        },
        "strict_historical_T0": "NOT_PROVEN",
        "reason": (
            "MISSING_HISTORICAL_RECEIPT_OR_VINTAGE_FIELDS"
            if not PROVENANCE_COLUMNS <= cols
            or "historical_candle_revisions" not in tables
            else "SCHEMA_IS_NOT_PROOF_OF_AS_KNOWN_AT_T0"
        ),
    }


def read_v2(path: Path, source_sha: str) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("report_schema_version") != "D1_RECONSTRUCTED_WARMUP_AUDIT_V2":
        raise ValueError("expected V2 research report")
    if data.get("calendar_origin") != "RECONSTRUCTED_MOEX_CURRENT_QUERY":
        raise ValueError("unexpected calendar origin")
    if data.get("strict_historical_as_known_at_T0") is not False:
        raise ValueError("research report must not claim strict T0")
    if data.get("original_unchanged") is not True:
        raise ValueError("unverified source archive")
    if any(data.get(key) != source_sha for key in (
        "source_sha256_before", "source_sha256_after"
    )):
        raise ValueError("research report archive SHA mismatch")
    markets = data.get("markets", {})
    if set(markets) != {"BR", "Si", "GOLD"}:
        raise ValueError("incomplete reconstructed market set")
    summary = {}
    for root, market in markets.items():
        if market.get("status") != "RECONSTRUCTED_RESEARCH_ONLY":
            raise ValueError(f"{root}: invalid research status")
        candidate = market.get("candidate_sessions")
        eligible = market.get("eligible")
        excluded = market.get("excluded")
        if not all(isinstance(v,int) for v in (candidate,eligible,excluded)) or (
            candidate != eligible + excluded
        ):
            raise ValueError(f"{root}: mismatched eligibility counts")
        contracts = market.get("per_contract", {})
        if len(contracts) != market.get("contracts"):
            raise ValueError(f"{root}: mismatched contract count")
        for secid, rec in contracts.items():
            if (rec.get("origin") != "RECONSTRUCTED_MOEX"
                or len(rec.get("expected_dates", ())) != 20
                or rec.get("expected_count") != 20
                or not rec.get("evidence_key")):
                raise ValueError(f"{root}/{secid}: missing exact date evidence")
        summary[root] = {
            "candidate_sessions": candidate,
            "eligible_reconstructed": eligible,
            "excluded_reconstructed": excluded,
            "contracts": len(contracts),
        }
    return {
        "file_sha256": sha256(path),
        "manifest_sha256_reported": data.get("evidence_manifest_sha256"),
        "scope": "RECONSTRUCTED_RESEARCH_ONLY",
        "markets": summary,
    }


def audit(source: Path, v2: Path | None = None) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    before = sha256(source)
    import duckdb
    db = duckdb.connect(str(source), read_only=True)
    try:
        evidence = summarize_schema(db)
    finally:
        db.close()
    reconstructed = read_v2(v2, before) if v2 else None
    after = sha256(source)
    if before != after:
        raise RuntimeError("archive changed during read-only audit")
    return {
        "audit_version": "D1_PIT_PROVENANCE_INVENTORY_V0",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256_before": before,
        "source_sha256_after": after,
        "source_unchanged": True,
        "database_opened_read_only": True,
        "schema_evidence": evidence,
        "reconstructed_v2": reconstructed,
        "decision": "RECONSTRUCTED_ONLY_STRICT_HISTORICAL_CAUSALITY_UNPROVEN",
        "warning": "Inferred available_at and completed candle end are not "
                   "historical first-receipt/version evidence; no T0 "
                   "promotion, threshold calibration or holdout acceptance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--v2", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.archive, args.v2), indent=2))


if __name__ == "__main__":
    main()
