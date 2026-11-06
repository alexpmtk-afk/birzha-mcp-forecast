"""Read-only RI Purnov data readiness inspection of canonical Google rows.

Counts describe existing market-data/evidence fields, NOT completed Purnov
rules, causal PIT attestation, exact order flow, or forecast fitness.
No network, model calculation, mutation, local storage or data refresh.
"""
from __future__ import annotations

from collections import Counter

from birzha.application.model3_indicator_coverage import (
    COMPACT_RI_INDICATOR_COLUMNS, coverage_summary,
)

# These are the existing approved 95-column RI research fields. Values in
# columns ending with _approx/_proxy are NEVER classified as true tape data.
FEATURE_FIELDS = (
    "p3_range", "p3_body", "p3_close_location",
    "p3_prior_atr14", "p3_range_prior_atr",
    "p3_rvol_same_clock20_approx", "p3_prior_high", "p3_prior_low",
    "p3_structure", "p3_pivot_confirmation_time",
    "p3_prior_confirmed_high", "p3_prior_confirmed_low",
    "p3_wave_anchor_time", "p3_wave_progress",
    "p3_failed_up_break", "p3_failed_down_break",
    "p3_candidate_state", "p3_minute_sign_delta_proxy",
    "p3_profile_poc_approx", "p3_profile_val_approx",
    "p3_profile_vah_approx", "p3_profile_hvn_approx_json",
    "p3_profile_lvn_approx_json",
    "p3_signal_eligible",
)


def _present(value):
    return value is not None and value != ""


def _bool(value):
    if type(value) is bool:
        return value
    if isinstance(value, str):
        if value.upper() == "TRUE":
            return True
        if value.upper() == "FALSE":
            return False
    raise ValueError("MODEL3_BOOLEAN_FIELD_INVALID")


def readiness_summary(candles, indicators, *, timeframe):
    """Audit exact RI rows without producing a signal or reading future outcomes.

    The caller has already pinned the RI Google book. Historical source first
    receipt/availability is not proven merely by a nonempty timestamp.
    """
    if timeframe not in ("D1", "H1", "M15"):
        raise ValueError("MODEL3_TIMEFRAME_UNSUPPORTED")
    from birzha.application.model3_google_backfill import scope_2026
    candles, indicators = scope_2026(candles, indicators)
    base = coverage_summary(candles, indicators, timeframe=timeframe)
    header = tuple(indicators[0])
    if header != COMPACT_RI_INDICATOR_COLUMNS:
        raise ValueError("MODEL3_REQUIRES_EXISTING_95_COLUMN_SCHEMA")
    source_head = tuple(candles[0])
    required_source = (
        "record_key", "contract_code", "trade_session_date",
        "bar_start_time", "bar_end_time", "available_at",
        "is_complete", "normalized_sha256",
    )
    if any(k not in source_head for k in required_source):
        raise ValueError("MODEL3_CANDLE_METADATA_REQUIRED")
    sh = {x: source_head.index(x) for x in required_source}
    ih = {x: header.index(x) for x in FEATURE_FIELDS}
    ih.update({x: header.index(x) for x in (
        "source_record_key", "available_at_confidence", "calendar_status",
        "source_complete_flag", "quality_status", "p3_delta_proxy_status",
        "p3_profile_status",
    )})
    source = {}
    source_complete = Counter()
    source_hash_count = source_session_count = source_available_count = 0
    contracts = Counter()
    for r in candles[1:]:
        key = r[sh["record_key"]]
        source[key] = r
        contracts[str(r[sh["contract_code"]])] += 1
        source_complete["complete" if _bool(r[sh["is_complete"]]) else "incomplete"] += 1
        source_hash_count += _present(r[sh["normalized_sha256"]])
        source_session_count += _present(r[sh["trade_session_date"]])
        source_available_count += _present(r[sh["available_at"]])
    counts = {f: 0 for f in FEATURE_FIELDS}
    calendar = Counter()
    availability = Counter()
    quality = Counter()
    candidate_states = Counter()
    indicator_complete = Counter()
    eligible_claims = 0
    source_indicator_disagreements = 0
    for original in indicators[1:]:
        row = list(original) + [""] * (len(header) - len(original))
        for f in FEATURE_FIELDS:
            counts[f] += _present(row[ih[f]])
        calendar[str(row[ih["calendar_status"]])] += 1
        availability[str(row[ih["available_at_confidence"]])] += 1
        quality[str(row[ih["quality_status"]])] += 1
        candidate_states[str(row[ih["p3_candidate_state"]])] += 1
        flag = _bool(row[ih["source_complete_flag"]])
        indicator_complete["complete" if flag else "incomplete"] += 1
        eligible_claims += _bool(row[ih["p3_signal_eligible"]])
        source_row = source.get(row[ih["source_record_key"]])
        if source_row is not None and _bool(source_row[sh["is_complete"]]) != flag:
            source_indicator_disagreements += 1

    n = len(indicators) - 1
    pit_approved = False  # This endpoint NEVER attests historical first receipt.
    data_gates = {
        "source_first_seen_historical": "UNVERIFIED",
        "exchange_trade_date_per_bar": "UNVERIFIED",
        "exact_aggressor_delta": "UNAVAILABLE",
        "exact_volume_at_price": "UNAVAILABLE",
        "research_only": True,
        "historical_PIT_admission": pit_approved,
        "forecast_admission": "NOT_GRANTED",
    }
    return {
        "status": "READ_ONLY_RESEARCH_DATA_READINESS",
        "symbol": "RI",
        "year": 2026,
        "timeframe": timeframe,
        "source_records": len(source),
        "indicator_records": n,
        "record_coverage": base,
        "raw_complete": dict(source_complete),
        "raw_with_trade_session_date": source_session_count,
        "raw_with_available_at_value_not_attested": source_available_count,
        "raw_with_source_sha": source_hash_count,
        "source_contract_counts": dict(sorted(contracts.items())),
        "feature_nonempty_counts": counts,
        "indicator_complete": dict(indicator_complete),
        "indicator_source_completeness_disagreements": source_indicator_disagreements,
        "indicator_available_at_confidence": dict(availability),
        "indicator_calendar_status": dict(calendar),
        "indicator_quality_status": dict(quality),
        "candidate_state_counts_observation_only": dict(candidate_states),
        "signal_eligible_claims_not_admitted": eligible_claims,
        "data_gates": data_gates,
        "written": False,
    }
