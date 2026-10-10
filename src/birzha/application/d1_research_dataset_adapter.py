"""Strict, read-only decoding of the pinned six-market research artifacts.

A caller-supplied manifest SHA anchors trust outside the artifact bundle.
This verifies artifact consistency, never the truth of the historical calendar.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from math import isfinite
from pathlib import Path
import re

from birzha.application.d1_research_features import (
    D1_RESEARCH_ATR_METHOD, D1_RESEARCH_ELIGIBILITY,
    D1_RESEARCH_FEATURE_VERSION, D1ResearchFeatureSet, ResearchFeature,
)

ADAPTER_VERSION = "G2_D1_DATASET_ADAPTER_V1"
DATASET_VERSION = "G2_SIX_MARKET_D1_DEVELOPMENT_DATASET_V1"
MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
REQUIRED = ("atr14_sma_tr", "d20_atr", "er20", "w20_atr")
ORIGINS = {"RECONSTRUCTED_MOEX", "CAPTURED_AT_SYNC", "ARCHIVED_ACTIVE_ROOT_CALENDAR"}
STATUSES = {"AVAILABLE", "UNAVAILABLE", "NOT_APPLICABLE", "INSUFFICIENT_HISTORY"}


class DatasetValidationError(ValueError):
    """Whole-bundle rejection: no partial dataset is returned."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetValidationError(message)


def _object(value: object, where: str) -> dict:
    _check(isinstance(value, dict), f"{where}: expected object")
    return value


def _text(value: object, where: str) -> str:
    _check(isinstance(value, str) and bool(value.strip()), f"{where}: expected nonempty text")
    return value


def _integer(value: object, where: str) -> int:
    _check(type(value) is int and value >= 0, f"{where}: expected nonnegative integer")
    return value


def _sha(value: object, where: str) -> str:
    _check(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
           f"{where}: expected lowercase SHA256")
    return value


def _day(value: object, where: str) -> str:
    _text(value, where)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DatasetValidationError(f"{where}: invalid date") from exc
    _check(parsed.isoformat() == value, f"{where}: expected YYYY-MM-DD")
    return value


def _number(value: object, where: str) -> float:
    _check(type(value) in (int, float), f"{where}: expected finite non-boolean number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise DatasetValidationError(f"{where}: number outside finite range") from exc
    _check(isfinite(converted), f"{where}: nonfinite number")
    return converted


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        _check(name not in result, f"duplicate JSON key: {name}")
        result[name] = value
    return result


def _constant(value: str) -> None:
    raise DatasetValidationError(f"nonstandard JSON constant: {value}")


def _json_float(value: str) -> float:
    return _number(float(value), "JSON number")


def _decode(payload: bytes, where: str) -> dict:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant, parse_float=_json_float)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DatasetValidationError(f"{where}: invalid JSON: {exc}") from exc
    return _object(value, where)


def _jsonl(payload: bytes, where: str) -> tuple[dict, ...]:
    # The pinned extractor emits one LF-terminated object per row, including
    # no bytes at all for a legitimately empty output.
    if not payload:
        return ()
    _check(payload.endswith(b"\n"), f"{where}: missing final LF")
    lines = payload[:-1].split(b"\n")
    _check(all(line.strip() for line in lines), f"{where}: blank row")
    return tuple(_decode(line, f"{where} line {i}") for i, line in enumerate(lines, 1))


@dataclass(frozen=True, slots=True)
class AdaptedResearchRow:
    market: str
    original_json: str
    features: D1ResearchFeatureSet


@dataclass(frozen=True, slots=True)
class ValidatedResearchDataset:
    manifest_json: str
    manifest_sha256: str
    accepted_rows_sha256: str
    exclusion_rows_sha256: str
    rows: tuple[AdaptedResearchRow, ...]
    exclusions_json: tuple[str, ...]
    adapter_version: str = ADAPTER_VERSION


def _identity(row: dict, manifest: dict, seen: set, where: str) -> tuple[str, str, str]:
    market = _text(row.get("market"), where + ".market")
    _check(market in MARKETS, f"{where}: unsupported market")
    secid = _text(row.get("secid"), where + ".secid")
    session = _day(row.get("session"), where + ".session")
    _check(manifest["development_from"] <= session <= manifest["development_till"],
           f"{where}: session outside Development")
    identity = (market, session)
    _check(identity not in seen, f"{where}: duplicate market/session")
    seen.add(identity)
    return market, secid, session


def _adapt(row: dict, manifest: dict, seen: set, where: str) -> AdaptedResearchRow:
    market, secid, session = _identity(row, manifest, seen, where)
    if market in ("SBER", "IMOEX", "RTSI"):
        _check(secid == market, f"{where}: instrument identity mismatch")
    else:
        prefix = {"Si": "Si", "BR": "BR", "GOLD": "GD"}[market]
        _check(re.fullmatch(prefix + r"[FGHJKMNQUVXZ][0-9]{1,2}", secid) is not None,
               f"{where}: futures contract family mismatch")
    expected = row.get("expected_sessions")
    _check(isinstance(expected, list) and len(expected) == 21,
           f"{where}: expected exactly 21 sessions")
    days = [_day(day, where + ".expected_sessions") for day in expected]
    _check(days == sorted(set(days)) and days[-1] == session,
           f"{where}: unordered/duplicate/future sessions or wrong endpoint")
    # Pre-2021 warmup is allowed by the extractor; the classified endpoint,
    # not every warmup bar, must be within Development.
    _check("decision_knowledge_cutoff_at" in row
           and row["decision_knowledge_cutoff_at"] is None,
           f"{where}: historical decision must remain NULL")
    _check(row.get("historical_first_receipt") == "NOT_PROVEN",
           f"{where}: historical receipt claim")
    _check(row.get("source") == "HISTORICAL_ARCHIVE_RECONSTRUCTED",
           f"{where}: unsupported source")
    event = _text(row.get("bar_event_end_at"), where + ".bar_event_end_at")
    try:
        event_time = datetime.fromisoformat(event)
    except ValueError as exc:
        raise DatasetValidationError(f"{where}: invalid bar end") from exc
    _check(len(event) >= 19 and event_time.date().isoformat() == session,
           f"{where}: bar end date mismatch")
    origin = _text(row.get("calendar_evidence_origin"), where + ".calendar_evidence_origin")
    _check(origin in ORIGINS, f"{where}: unsupported calendar origin")
    key = _text(row.get("calendar_evidence_key"), where + ".calendar_evidence_key")
    features = _object(row.get("features"), where + ".features")
    _check(features.get("schema") == D1_RESEARCH_FEATURE_VERSION,
           f"{where}: feature version mismatch")
    _check(features.get("atr_method") == D1_RESEARCH_ATR_METHOD, f"{where}: ATR method mismatch")
    _check(features.get("eligibility") == D1_RESEARCH_ELIGIBILITY
           and features.get("strict_historical_as_known_at_t0") is False,
           f"{where}: unsupported feature evidence scope")
    _check(features.get("secid") == secid and features.get("session") == session
           and features.get("evidence_origin") == origin and features.get("evidence_key") == key,
           f"{where}: row/feature identity mismatch")
    _check(_integer(features.get("observed_bars"), where + ".observed_bars") == 21,
           f"{where}: wrong observed bar count")
    items = _object(features.get("features"), where + ".feature_values")
    _check(all(name in items for name in REQUIRED), f"{where}: missing required feature")
    converted = []
    numbers = {}
    for name, raw in sorted(items.items()):
        _text(name, where + ".feature_name")
        item = _object(raw, where + "." + name)
        _check(set(item) == {"value", "status", "reason"}, f"{where}.{name}: feature fields")
        status, value, reason = item["status"], item["value"], item["reason"]
        _check(isinstance(status, str) and status in STATUSES,
               f"{where}.{name}: invalid feature status")
        _check(reason is None or isinstance(reason, str), f"{where}.{name}: invalid reason")
        if status == "AVAILABLE":
            numbers[name] = _number(value, f"{where}.{name}")
        else:
            _check(value is None, f"{where}.{name}: unavailable value must remain NULL")
        _check(name not in REQUIRED or status == "AVAILABLE",
               f"{where}.{name}: admitted row has unavailable required feature")
        converted.append((name, ResearchFeature(value, status, reason)))
    _check(numbers["atr14_sma_tr"] > 0 and 0 <= numbers["er20"] <= 1
           and numbers["w20_atr"] >= 0, f"{where}: required feature outside domain")
    feature_set = D1ResearchFeatureSet(
        secid=secid, session=session, evidence_origin=origin, evidence_key=key,
        observed_bars=21, features=tuple(converted),
    )
    return AdaptedResearchRow(market, canonical_json(row), feature_set)


def validate_d1_research_dataset(
    manifest_bytes: bytes, accepted_bytes: bytes, exclusion_bytes: bytes, *,
    trusted_manifest_sha256: str,
) -> ValidatedResearchDataset:
    """Validate the entire bundle before exposing any row. Never writes files."""
    expected_sha = _sha(trusted_manifest_sha256, "trusted_manifest_sha256")
    _check(sha256(manifest_bytes) == expected_sha, "trusted manifest SHA mismatch")
    manifest = _decode(manifest_bytes, "manifest")
    _check(manifest.get("schema") == DATASET_VERSION, "manifest: schema mismatch")
    _check(manifest.get("scope") == D1_RESEARCH_ELIGIBILITY
           and manifest.get("strict_historical_as_known_at_T0") is False,
           "manifest: unsupported evidence scope")
    _check(manifest.get("feature_version") == D1_RESEARCH_FEATURE_VERSION,
           "manifest: feature version mismatch")
    markets = manifest.get("markets")
    _check(isinstance(markets, list) and len(markets) == len(MARKETS)
           and all(isinstance(m, str) for m in markets)
           and set(markets) == set(MARKETS), "manifest: six markets required")
    start = _day(manifest.get("development_from"), "manifest.development_from")
    end = _day(manifest.get("development_till"), "manifest.development_till")
    _check("2021-01-01" <= start <= end <= "2022-12-31",
           "manifest: unsupported Development period")
    for field in ("source_sha256_before_after", "v2_report_sha256", "extractor_code_sha256",
                  "feature_code_sha256", "accepted_rows_sha256", "exclusion_rows_sha256"):
        _sha(manifest.get(field), "manifest." + field)
    _check(sha256(accepted_bytes) == manifest["accepted_rows_sha256"], "accepted rows SHA mismatch")
    _check(sha256(exclusion_bytes) == manifest["exclusion_rows_sha256"], "exclusion rows SHA mismatch")
    warnings = manifest.get("warnings")
    _check(isinstance(warnings, list) and all(isinstance(w, str) for w in warnings),
           "manifest: warnings must be text list")
    accepted = _jsonl(accepted_bytes, "accepted")
    excluded = _jsonl(exclusion_bytes, "excluded")
    _check(_integer(manifest.get("accepted_rows"), "manifest.accepted_rows") == len(accepted)
           and _integer(manifest.get("excluded_rows"), "manifest.excluded_rows") == len(excluded),
           "manifest: total row counts mismatch")
    seen = set()
    rows = tuple(_adapt(row, manifest, seen, f"accepted[{i}]")
                 for i, row in enumerate(accepted, 1))
    rejected = {market: Counter() for market in MARKETS}
    for i, row in enumerate(excluded, 1):
        where = f"excluded[{i}]"
        market, _, _ = _identity(row, manifest, seen, where)
        reason = _text(row.get("reason"), where + ".reason")
        rejected[market][reason] += 1
    per_market = _object(manifest.get("per_market"), "manifest.per_market")
    _check(set(per_market) == set(MARKETS), "manifest: market totals missing/extra")
    admitted = Counter(row.market for row in rows)
    for market in MARKETS:
        totals = _object(per_market[market], "manifest." + market)
        n = _integer(totals.get("development_active_candidates"), market + ".candidates")
        a = _integer(totals.get("admitted_reconstructed"), market + ".admitted")
        e = _integer(totals.get("excluded"), market + ".excluded")
        reasons = _object(totals.get("exclusion_reasons"), market + ".reasons")
        for reason, count in reasons.items():
            _text(reason, market + ".reason")
            _check(_integer(count, market + ".reason_count") > 0, market + ": zero reason count")
        _check(a == admitted[market] and e == sum(rejected[market].values())
               and n == a + e and reasons == dict(rejected[market]),
               market + ": candidate/admission/exclusion accounting mismatch")
    return ValidatedResearchDataset(
        manifest_json=canonical_json(manifest), manifest_sha256=expected_sha,
        accepted_rows_sha256=sha256(accepted_bytes), exclusion_rows_sha256=sha256(exclusion_bytes),
        rows=rows, exclusions_json=tuple(canonical_json(row) for row in excluded),
    )


def read_d1_research_dataset(
    manifest_path: Path, accepted_path: Path, exclusion_path: Path, *,
    trusted_manifest_sha256: str,
) -> ValidatedResearchDataset:
    """Read each file once; validate its exact bytes. No archive or network I/O."""
    return validate_d1_research_dataset(
        manifest_path.read_bytes(), accepted_path.read_bytes(), exclusion_path.read_bytes(),
        trusted_manifest_sha256=trusted_manifest_sha256,
    )
