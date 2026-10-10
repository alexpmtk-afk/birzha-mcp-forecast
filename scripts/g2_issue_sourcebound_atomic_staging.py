"""Strict one-shot source-receipt → issued Forecast → one atomic DuckDB staging file.

Never accesses production journals, HOME, credentials, protected OOS or trading.
MOEX response bytes and local receipt declarations are verified for internal
integrity, NOT externally signed/independently time attested. No live fetch.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from birzha.application.prospective_capture import AdmissionRefused, CaptureEvidence
from birzha.application.prospective_issuance import prepare_source_bound_forecast
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from scripts.g2_freeze_real_forecast_from_source_receipts import (
    EXPECTED_SOURCE, assemble_snapshot,
)

VERSION = "G2_SOURCE_BOUND_ATOMIC_STAGING_ISSUER_V1"


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdmissionRefused("duplicate key in source manifest")
        result[key] = value
    return result


def _manifest_and_entry(source_dir: Path, market: str):
    if not source_dir.is_dir() or source_dir.is_symlink():
        raise AdmissionRefused("existing ordinary source directory required")
    manifest_file = source_dir / "manifest.json"
    if not manifest_file.is_file() or manifest_file.is_symlink():
        raise AdmissionRefused("missing original raw-source manifest")
    raw_manifest = manifest_file.read_bytes()
    try:
        manifest = json.loads(raw_manifest, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise AdmissionRefused("invalid original source manifest") from exc
    if (manifest.get("schema") != EXPECTED_SOURCE
        or manifest.get("origin") != "REAL_MOEX_PUBLIC_ISS_CAPTURE"):
        raise AdmissionRefused("unsupported or reconstructed source origin")
    if not isinstance(market, str) or not market or market not in manifest.get("requested_markets", ()):
        raise AdmissionRefused("market not explicitly in source capture request")
    entries = manifest.get("instruments")
    if not isinstance(entries, list) or len([x for x in entries if x.get("market") == market]) != 1:
        raise AdmissionRefused("market needs one complete captured instrument")
    entry = next(x for x in entries if x["market"] == market)
    if entry.get("status") != "SOURCE_CAPTURE_COMPLETE":
        raise AdmissionRefused("source capture not complete")
    if set(entry.get("timeframes", {})) != {"D1", "H1", "M1"}:
        raise AdmissionRefused("exact D1/H1/M1 pages required for D1/H1/M15")
    complete_markets = manifest.get("complete_markets")
    if complete_markets is not None and market not in complete_markets:
        raise AdmissionRefused("selected market absent from completed source manifest")
    listed = manifest.get("all_payload_files")
    if not isinstance(listed, list) or not listed:
        raise AdmissionRefused("missing original page inventory")
    index = {}
    for item in listed:
        name, digest = item.get("name"), item.get("sha256")
        if (not isinstance(name, str) or not name.endswith(".json")
            or Path(name).name != name or name in index
            or not isinstance(digest, str) or len(digest) != 64):
            raise AdmissionRefused("ambiguous or unsafe source page inventory")
        index[name] = digest
    actual_files = {p.name for p in source_dir.iterdir() if p.name != "manifest.json"}
    if actual_files != set(index):
        raise AdmissionRefused("source directory and original page inventory differ")
    used = set()
    for tf in ("D1", "H1", "M1"):
        meta = entry["timeframes"][tf]
        if meta.get("exact_secid") != entry.get("resolved_secid"):
            raise AdmissionRefused("source SECID or timeframe conflict")
        for page in meta.get("pages", []):
            name = page.get("path")
            if not isinstance(name, str) or name not in index or name in used:
                raise AdmissionRefused("missing or duplicate original source page")
            p = source_dir / name
            if not p.is_file() or p.is_symlink() or index[name] != page.get("sha256"):
                raise AdmissionRefused("source page identity differs from original manifest")
            if sha256(p.read_bytes()).hexdigest() != index[name]:
                raise AdmissionRefused("source page digest mismatch")
            used.add(name)
        if not meta.get("pages"):
            raise AdmissionRefused("empty source timeframe page inventory")
    if any(f.get("market") == market for f in manifest.get("failed_markets", [])):
        raise AdmissionRefused("market is also flagged incomplete")
    return entry, sha256(raw_manifest).hexdigest()


def issue_atomic_staging(
    source_dir: str | Path, staging_root: str | Path, market: str, *,
    clock=lambda: datetime.now(timezone.utc), fault_hook=None,
):
    """Issue from a *fresh* original receipt inventory, never an offline backtest.

    Requires an existing explicit disposable directory. A source bundle cannot
    be reissued to another Forecast ID for the same SECID. The duplicate guard
    and source+Forecast commit share the store's own reentrant lock.
    """
    source_dir = Path(source_dir).resolve()
    root = Path(staging_root).resolve()
    if root == source_dir or not root.is_dir() or not market or not isinstance(market, str):
        raise AdmissionRefused("separate existing disposable staging root required")
    if (market != Path(market).name or "/" in market or "\\" in market
        or market in (".", "..")):
        raise AdmissionRefused("unsafe market identifier for staging")
    entry, manifest_sha = _manifest_and_entry(source_dir, market)
    snapshot, bundle, observed, completed, counts, incomplete_m15 = assemble_snapshot(
        entry, source_dir, clock=clock
    )
    evidence = CaptureEvidence(
        source_payload=bundle, source_observed_at=observed,
        latest_completed_event_end=completed, source_origin="LIVE_CAPTURED_PAYLOAD",
        contract_version=snapshot.contract_version
    )
    prepared = prepare_source_bound_forecast(snapshot, evidence, clock=clock)
    # Store itself refuses HOME/production destinations and unrelated files.
    destination = root / f"{market}.duckdb"
    store = SingleDuckDBProspectiveStagingJournal(
        destination, staging_root=root, clock=clock, fault_hook=fault_hook
    )
    try:
        with store.lock:
            existing = store.db.execute(
                "SELECT forecast_id, receipt_json FROM g2_atomic_captures WHERE source_sha=?",
                [sha256(bundle).hexdigest()]
            ).fetchall()
            for prior_id, prior_raw in existing:
                previous = json.loads(prior_raw)
                if (previous.get("secid") == prepared.record.secid
                    and prior_id != prepared.record.forecast_id):
                    raise AdmissionRefused(
                        "same original SECID source bytes cannot be reissued with a new T0"
                    )
            capture = store.capture(prepared.record, prepared.evidence)
            audit = store.audit()
            if audit["captures"] < 1 or audit["outcomes"] != 0:
                raise AdmissionRefused("new source-bound issuance has unexpected staging state")
        return {
            "schema": VERSION,
            "status": capture["status"],
            "market": market, "secid": prepared.record.secid,
            "forecast_id": prepared.record.forecast_id,
            "snapshot_id": prepared.record.snapshot_id,
            "issued_at_t0": prepared.record.created_at_t0,
            "source_observed_at": observed,
            "source_bundle_sha256": sha256(bundle).hexdigest(),
            "original_source_manifest_sha256": manifest_sha,
            "timeframe_candles": counts,
            "discarded_m15_partial_buckets": incomplete_m15,
            "canonical_atomic_audit": audit,
            "staging_file": destination.name,
            "source_receipt_local_integrity_verified": True,
            "independent_provider_and_clock_attestation": False,
            "independent_exchange_session_calendar_verified": False,
            "strict_ex_ante_proof": False,
            "model_skill_proven": False,
            "production_authorized": False,
        }
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--staging-root", required=True, type=Path)
    parser.add_argument("--market", required=True)
    args = parser.parse_args(argv)
    result = issue_atomic_staging(args.source_dir, args.staging_root, args.market)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
