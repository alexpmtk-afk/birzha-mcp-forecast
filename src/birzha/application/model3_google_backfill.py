"""Cloud-only RI research indicator planner for the already-approved 95-column layout.

This module does not access the network or mutate cells. Only completed,
source-pinned research candles are used. Output is NOT a trading signal.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime
from hashlib import sha256
import json

from birzha.application.model3_features import research_features
from birzha.application.model3_indicator_coverage import (
    COMPACT_RI_INDICATOR_COLUMNS, coverage_summary, project_calculated_rows,
)
from birzha.application.canonical_market_mirror import MirrorConflict, digest
from birzha.application.google_first_archive import stamp


def _ordered(source, timefield, contractfield):
    head = source[0]
    ti, si = head.index(timefield), head.index(contractfield)
    def sort_key(row):
        return (stamp(row[ti]), str(row[si]))
    return sorted(source[1:], key=sort_key)


def plan_ri_indicator_backfill(*, candles, daily, existing, timeframe,
                               from_date, till_date, max_rows, calculated_at):
    """Return an exact, bounded insertion plan plus complete rows.

    New output uses only prior source candles, always with research-only labels.
    Existing 2026 research rows are never recalculated/overwritten by this path.
    The sorted insertion must not cross any existing row, so one atomic
    insertDimension+updateCells preserves their original content and order.
    """
    if timeframe not in ('D1', 'H1', 'M15'):
        raise MirrorConflict('MODEL3_TIMEFRAME_UNSUPPORTED')
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or not 1 <= max_rows <= 50:
        raise MirrorConflict('MODEL3_MAX_ROWS_BOUNDED_50')
    start, finish = date.fromisoformat(from_date), date.fromisoformat(till_date)
    if start < date(2025, 1, 1) or not 0 <= (finish - start).days <= 30:
        raise MirrorConflict('MODEL3_DATE_RANGE_BOUNDED_31')
    if not isinstance(calculated_at, str) or not calculated_at:
        raise MirrorConflict('MODEL3_CALCULATED_AT_REQUIRED')
    if not candles or not daily or not existing:
        raise MirrorConflict('MODEL3_INPUT_REQUIRED')
    if tuple(existing[0]) != COMPACT_RI_INDICATOR_COLUMNS:
        raise MirrorConflict('MODEL3_COMPACT_95_ONLY')
    coverage = coverage_summary(candles, existing, timeframe=timeframe)
    col = {name: candles[0].index(name) for name in ('record_key', 'bar_start_time', 'contract_code', 'is_complete')}
    ih = existing[0]
    source_col = ih.index('source_record_key')
    begin_col = ih.index('begin')
    secid_col = ih.index('secid')
    known = {str(r[source_col]) for r in existing[1:]}
    existing_sorted = []
    for r in existing[1:]:
        r = list(r) + [''] * (len(ih) - len(r))
        if not isinstance(r[begin_col], str) or not r[begin_col]:
            raise MirrorConflict('MODEL3_EXISTING_TIME_MISSING')
        existing_sorted.append(((stamp(r[begin_col]), str(r[secid_col])), r))
    if existing_sorted != sorted(existing_sorted, key=lambda x:x[0]):
        raise MirrorConflict('MODEL3_EXISTING_INDICATORS_UNSORTED')
    expected_keys = [x[0] for x in existing_sorted]
    selected = []
    for row in _ordered(candles, 'bar_start_time', 'contract_code'):
        when = stamp(row[col['bar_start_time']]).date()
        if start <= when <= finish and row[col['record_key']] not in known:
            selected.append(row)
    if not selected:
        return {'status': 'MODEL3_NO_MISSING_IN_WINDOW', 'written': False, 'planned_rows': 0,
                'coverage': coverage, 'insert_row_index': None}, []
    first = selected[0]
    first_key = (stamp(first[col['bar_start_time']]), str(first[col['contract_code']]))
    after_index = bisect_left(expected_keys, first_key)
    # The entire request must fit in one chronological gap between accepted rows.
    upper_bound = expected_keys[after_index] if after_index < len(expected_keys) else None
    chosen = []
    for row in selected:
        key = (stamp(row[col['bar_start_time']]), str(row[col['contract_code']]))
        if upper_bound is not None and key >= upper_bound:
            break
        if len(chosen) == max_rows:
            break
        chosen.append(row)
    if not chosen:
        raise MirrorConflict('MODEL3_NO_CONTIGUOUS_INSERT')
    chosen_keys = {r[col['record_key']] for r in chosen}

    def cutoff(table):
        h = table[0]
        ti = h.index('bar_start_time')
        return [h] + [r for r in table[1:]
                      if stamp(r[ti]).date() <= finish]
    inputs = {'D1': cutoff(daily)}
    if timeframe != 'D1':
        inputs[timeframe] = cutoff(candles)
    # Reuse the already-versioned research implementation; never invent delta,
    # exact volume-at-price, availability timestamps or session/calendar labels.
    generated = research_features(inputs, targets=chosen_keys, calculated_at=calculated_at)
    projected = project_calculated_rows(generated[timeframe], existing[0])
    by_source = {r[source_col]: r for r in projected}
    if len(by_source) != len(chosen) or set(by_source) != chosen_keys:
        raise MirrorConflict('MODEL3_GENERATED_SOURCE_KEYS_MISMATCH')
    output = [by_source[r[col['record_key']]] for r in chosen]
    if any(len(r) != 95 for r in output):
        raise MirrorConflict('MODEL3_ROW_WIDTH_MISMATCH')
    return {'status': 'MODEL3_RESEARCH_BACKFILL_PLAN', 'written': False,
            'timeframe': timeframe, 'planned_rows': len(output),
            'remaining_window_records': len(selected) - len(output),
            'insert_row_index': after_index + 1,  # 0-based incl. header
            'source_keys': [r[col['record_key']] for r in chosen],
            'row_digest': digest([list(existing[0])]+output),
            'coverage': coverage, 'forecast_admission': 'NOT_GRANTED',
            'calendar': 'UNVERIFIED', 'historical_availability': 'UNKNOWN',
            'minute_features': 'UNAVAILABLE_UNLESS_SEPARATELY_ATTESTED',
            'indicator_columns': 95, 'calculated_at': calculated_at}, output
