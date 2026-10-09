"""Read-only-source, bounded MOEX reconstructed D1 warmup coverage audit.

Run from repository root (with dependencies installed):
    python -m scripts.d1_reconstructed_warmup_audit --source /path/to/archive.duckdb

This copies the archive into a temporary directory, rechecks exact-SECID
calendar dates from CURRENT public MOEX, stores RECONSTRUCTED_MOEX session
evidence in that disposable copy, and prints aggregate D1 eligibility.
Never treats a historical marker-only range as a complete calendar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from birzha.application.d1_research_eligibility import (
    D1ResearchEligibilityReason,
    ExactD1Candle,
    VerifiedD1Session,
    evaluate_d1_research_window_with_warmup,
    load_verified_d1_warmup_evidence,
)
from birzha.application.warmup_session_evidence import warmup_d1_evidence_key
from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.providers.moex_iss import MoexIssClient
from birzha.storage.historical_store import DuckDBHistoricalCandleStore

ROOTS = ("BR", "Si", "GOLD")
ROOT_SESSION_SUFFIX = "ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V2_ACTIVITY"
AUDIT_REPORT_SCHEMA_VERSION = "D1_RECONSTRUCTED_WARMUP_AUDIT_V2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _root_key(root: str) -> str:
    return f"{root}#{ROOT_SESSION_SUFFIX}"


def _stored_exact_candles(
    store: DuckDBHistoricalCandleStore, secid: str,
    from_day: str, till_day: str,
) -> dict[str, Any]:
    instrument = store.stored_instrument(secid)
    if instrument is None:
        return {}
    series = store.read(instrument, "D1", from_day, till_day)
    result: dict[str, Any] = {}
    for candle in series.candles:
        day = candle.begin[:10]
        if day in result:
            # Fail closed on duplicate dates even if begins differ.
            return {}
        result[day] = candle
    return result


def _reconstruct_one_warmup(
    store: DuckDBHistoricalCandleStore,
    calendar: MoexTradingCalendar,
    secid: str,
    first_active: str,
) -> tuple[str | None, str]:
    """Never certify preactive dates without the current exact MOEX response.

    Evidence is a fresh reconstructed snapshot. A previously stored legacy
    range alone is neither read nor accepted as sufficient.
    """
    instrument = store.stored_instrument(secid)
    if instrument is None or instrument.asset_class != "future":
        return None, "MISSING_INSTRUMENT"
    start = date.fromisoformat(first_active) - timedelta(days=300)
    finish = date.fromisoformat(first_active) - timedelta(days=1)
    try:
        days = calendar.dates(
            engine=instrument.engine, market=instrument.market,
            board=instrument.board, security=secid,
            from_date=start, till_date=finish,
        )
    except Exception as exc:
        # Failed exchange evidence is NOT an empty verified calendar.
        return None, f"MOEX_ERROR:{type(exc).__name__}"
    if not days:
        return None, "NO_MOEX_PREACTIVE_DATES"
    expected = tuple(d.isoformat() for d in days[-20:])
    if len(expected) < 20:
        return None, "INSUFFICIENT_MOEX_HISTORY"
    candles = _stored_exact_candles(store, secid, expected[0], expected[-1])
    for day in expected:
        candle = candles.get(day)
        if candle is None:
            return None, "MISSING_PREACTIVE_BAR"
        if not candle.completed or any(
            value is None for value in
            (candle.open, candle.high, candle.low, candle.close)
        ):
            return None, "INVALID_PREACTIVE_OHLC"
    key = warmup_d1_evidence_key(
        secid, expected, origin="RECONSTRUCTED_MOEX"
    )
    store.record_sessions(key, secid, expected)
    rows = store.stored_session_contracts(key, expected[0], expected[-1])
    if rows != tuple((day, secid) for day in expected):
        return None, "SESSION_EVIDENCE_MISMATCH"
    store.mark_session_range_verified(key, expected[0], expected[-1])
    return key, "RECONSTRUCTED_MOEX"


def audit_copy(
    store: DuckDBHistoricalCandleStore,
    calendar: MoexTradingCalendar,
    *,
    roots: tuple[str, ...] = ROOTS,
) -> dict[str, Any]:
    """All mutations are restricted to the disposable store supplied by caller."""
    results: dict[str, Any] = {}
    for root in roots:
        key = _root_key(root)
        sessions = store.stored_session_contracts(key, "0001-01-01", "9999-12-31")
        if not sessions:
            results[root] = {"status": "NO_ACTIVE_CALENDAR", "count": 0}
            continue
        seen = set()
        verified: list[VerifiedD1Session] = []
        ambiguous = False
        for day, secid in sessions:
            if day in seen or not store.is_session_range_verified(key, day, day):
                ambiguous = True
                break
            seen.add(day)
            verified.append(VerifiedD1Session(day, secid))
        if ambiguous:
            results[root] = {
                "status": "UNVERIFIED_ACTIVE_CALENDAR",
                "candidate_sessions": len(sessions),
            }
            continue

        per_secid: dict[str, list[str]] = defaultdict(list)
        for session in verified:
            per_secid[session.secid].append(session.trade_date)
        preactive: dict[str, Any] = {}
        reasons: Counter[str] = Counter()
        for secid, dates in per_secid.items():
            evidence_key, status = _reconstruct_one_warmup(
                store, calendar, secid, dates[0]
            )
            warmup = (
                load_verified_d1_warmup_evidence(store, secid, evidence_key)
                if evidence_key else None
            )
            # Serialize the actual verified generation before the scratch DB
            # disappears. A count by itself cannot reproduce MOEX's exact dates.
            # Empty evidence must remain explicit, never inferred from markers.
            preactive[secid] = {
                "status": status,
                "first_active_date": dates[0],
                "origin": warmup.origin if warmup else None,
                "evidence_key": warmup.evidence_key if warmup else None,
                "expected_dates": list(warmup.expected_dates) if warmup else [],
                "expected_count": len(warmup.expected_dates) if warmup else 0,
            }
            if warmup is None and evidence_key:
                preactive[secid]["status"] = "UNVERIFIED_WARMUP"

            instrument = store.stored_instrument(secid)
            if instrument is None:
                reasons["WRONG_INSTRUMENT_IDENTITY"] += len(dates)
                continue
            all_active_dates = tuple(day for day in dates)
            for offset, current in enumerate(all_active_dates):
                active_part = all_active_dates[max(0, offset - 20):offset + 1]
                needed = max(0, 21 - len(active_part))
                # Missing warmup evidence is deliberately passed as None, so
                # early-contract windows fail closed as UNVERIFIED_WARMUP.
                chosen = (
                    tuple(warmup.expected_dates[-needed:]) + active_part
                    if needed and warmup is not None
                    else active_part
                )
                if len(chosen) == 21:
                    raw = _stored_exact_candles(store, secid, chosen[0], chosen[-1])
                    candles = tuple(
                        ExactD1Candle(secid, raw[day])
                        for day in chosen if day in raw
                    )
                else:
                    candles = tuple(
                        ExactD1Candle(secid, candle)
                        for day, candle in _stored_exact_candles(
                            store, secid, active_part[0], active_part[-1]
                        ).items() if day in active_part
                    )
                reason = evaluate_d1_research_window_with_warmup(
                    instrument, candles, tuple(verified),
                    candidate_date=current, warmup=warmup,
                ).reason
                reasons[str(reason)] += 1
        results[root] = {
            "status": "RECONSTRUCTED_RESEARCH_ONLY",
            "candidate_sessions": len(verified),
            "contracts": len(per_secid),
            "contract_warmup_status": dict(Counter(
                rec["status"] for rec in preactive.values()
            )),
            "eligible": reasons.get("ELIGIBLE", 0),
            "excluded": len(verified) - reasons.get("ELIGIBLE", 0),
            "exclusion_reasons": dict(sorted(reasons.items())),
            "per_contract": preactive,
        }
    return results


def run(source: Path, *, roots: tuple[str, ...] = ROOTS) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    before = _sha256(source)
    audit_started_at_utc = datetime.now(timezone.utc).isoformat()
    audit_script_sha256 = _sha256(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="birzha-reconstructed-audit-") as tmp:
        scratch = Path(tmp) / "working_copy.duckdb"
        shutil.copyfile(source, scratch)
        if _sha256(scratch) != before:
            raise RuntimeError("archive copy checksum mismatch")
        store = DuckDBHistoricalCandleStore(str(scratch))
        try:
            report = audit_copy(store, MoexTradingCalendar(MoexIssClient()), roots=roots)
        finally:
            store.close()
    after = _sha256(source)
    if before != after:
        raise RuntimeError("original archive changed during audit")
    # The checksum covers the exact per-contract expected dates and immutable
    # generation key. It is stable across reruns if the calendars are unchanged.
    manifest = {
        root: {
            secid: {
                "first_active_date": record["first_active_date"],
                "origin": record["origin"],
                "evidence_key": record["evidence_key"],
                "expected_dates": record["expected_dates"],
            }
            for secid, record in market.get("per_contract", {}).items()
        }
        for root, market in report.items()
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    return {
        "report_schema_version": AUDIT_REPORT_SCHEMA_VERSION,
        "audit_started_at_utc": audit_started_at_utc,
        "audit_script_sha256": audit_script_sha256,
        "evidence_manifest_sha256": manifest_sha256,
        "source_sha256_before": before,
        "source_sha256_after": after,
        "original_unchanged": True,
        "calendar_origin": "RECONSTRUCTED_MOEX_CURRENT_QUERY",
        "strict_historical_as_known_at_T0": False,
        "markets": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--roots", nargs="+", choices=ROOTS, default=list(ROOTS))
    args = parser.parse_args()
    print(json.dumps(run(args.source, roots=tuple(args.roots)), indent=2))


if __name__ == "__main__":
    main()
