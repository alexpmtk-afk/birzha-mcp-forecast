"""Read-only RI Model 3 indicator coverage and exact-column projection.

This is NOT an indicator writer or a signal readiness gate. It preserves the
existing 95-column RI research layout without creating or widening sheets.
"""
from __future__ import annotations

from birzha.application.model3_features import BASE_COLUMNS, LEGACY_COLUMNS, COLUMNS, VERSION

# Observed in the canonical RI D1/H1/M15 indicator tabs on 2026-11-01.
# These are legacy/internal or unapproved continuous columns, NOT deleted data.
OMITTED_COMPACT_COLUMNS = frozenset({
    "source_window_sha256", "available_at", "atr_14_sma", "trend_score_legacy",
    "_atr_calc_key", "_atr_calc_time", "_atr_calc_contract", "_atr_calc_tr",
    "_atr_calc_valid", "_atr_calc_run_count", "_atr_calc_run_sum",
    "_atr_calc_value", "atr_source_record_key", "_atr_output_source_key",
    "continuous_version", "continuous_coordinate", "continuous_selected",
    "continuous_open", "continuous_high", "continuous_low", "continuous_close",
    "continuous_input_sha256", "continuous_trade_session", "continuous_roll_basis",
})
COMPACT_RI_INDICATOR_COLUMNS = tuple(c for c in COLUMNS if c not in OMITTED_COMPACT_COLUMNS)
if len(COMPACT_RI_INDICATOR_COLUMNS) != 95 or len(COMPACT_RI_INDICATOR_COLUMNS) != len(set(COMPACT_RI_INDICATOR_COLUMNS)):
    raise RuntimeError("RI compact indicator specification changed")
APPROVED_HEADERS = {
    "COMPACT_RI_95": COMPACT_RI_INDICATOR_COLUMNS,
    "BASE_LEGACY": tuple(BASE_COLUMNS),
    "P3_LEGACY": tuple(LEGACY_COLUMNS),
    "P3_EXTENDED": tuple(COLUMNS),
}


def indicator_header_type(header):
    """Never silently accept reordered, truncated or invented columns."""
    names = tuple(header)
    return next((name for name, approved in APPROVED_HEADERS.items() if names == approved), None)


def project_calculated_rows(rows, destination_header):
    """Project only generated research rows into the EXISTING approved header.

    No Google mutation is performed. A future writer must separately implement
    atomic/cloud-receipt writes, reconciliation and explicit sole-writer checks.
    """
    if indicator_header_type(destination_header) is None:
        raise ValueError("INDICATOR_HEADER_NOT_APPROVED")
    positions = [COLUMNS.index(name) for name in destination_header]
    result = []
    for row in rows:
        if len(row) != len(COLUMNS):
            raise ValueError("INDICATOR_GENERATED_ROW_WIDTH")
        result.append([row[i] for i in positions])
    return result


def coverage_summary(candles, indicators, *, timeframe):
    """Compare pinned exact-contract record keys without downloading or writing.

    Counts describe RECORD COVERAGE only. Feature availability, source PIT,
    formulas, session attestation and Forecast admission are NOT proved.
    """
    if timeframe not in ("D1", "H1", "M15"):
        raise ValueError("INDICATOR_TIMEFRAME")
    if not candles or not indicators:
        raise ValueError("INDICATOR_SOURCE_EMPTY")
    ch, ih = candles[0], indicators[0]
    if not {"record_key", "contract_code", "bar_start_time"}.issubset(ch):
        raise ValueError("CANDLE_HEADER_MISMATCH")
    kind = indicator_header_type(ih)
    if kind is None:
        raise ValueError("INDICATOR_HEADER_NOT_APPROVED")
    indices = {name: ch.index(name) for name in ("record_key", "contract_code", "bar_start_time")}
    src_idx = ih.index("source_record_key")
    symbol_idx = ih.index("symbol")
    tf_idx = ih.index("timeframe")
    candles_by_key = {}
    for row in candles[1:]:
        if len(row) != len(ch):
            raise ValueError("CANDLE_ROW_WIDTH")
        key = row[indices["record_key"]]
        if not isinstance(key, str) or not key.startswith("RI|" + timeframe + "|") or key in candles_by_key:
            raise ValueError("CANDLE_SOURCE_IDENTITY_OR_DUPLICATE")
        candles_by_key[key] = row
    seen = set()
    orphan = 0
    for row in indicators[1:]:
        if len(row) > len(ih):
            raise ValueError("INDICATOR_ROW_WIDTH")
        row = list(row) + [""] * (len(ih) - len(row))
        key = row[src_idx]
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("INDICATOR_SOURCE_IDENTITY_OR_DUPLICATE")
        if row[symbol_idx] != "RI" or row[tf_idx] != timeframe:
            raise ValueError("INDICATOR_INSTRUMENT_OR_TIMEFRAME")
        seen.add(key)
        if key not in candles_by_key:
            orphan += 1
    missing = [key for key in candles_by_key if key not in seen]
    return {
        "status": "READ_ONLY_COVERAGE",
        "symbol": "RI",
        "timeframe": timeframe,
        "indicator_schema": kind,
        "indicator_columns": len(ih),
        "candle_records": len(candles_by_key),
        "indicator_records": len(seen),
        "matched_records": len(seen) - orphan,
        "missing_indicator_records": len(missing),
        "orphan_indicator_records": orphan,
        "missing_record_keys_sample": missing[:5],
        "formula_version_checked": False,
        "source_availability_attested": False,
        "forecast_admission": "NOT_GRANTED",
        "research_formula_version": VERSION,
        "written": False,
    }
