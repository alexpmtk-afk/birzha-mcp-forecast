"""Pure candle-sequence auditor driven by explicit, versioned evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date

from birzha.domain.market import CandleSeries, Instrument
from birzha.domain.sequence_quality import (
    CANDLE_SEQUENCE_QUALITY_VERSION,
    CandleIntervalKey,
    CandleSequenceScheduleEvidence,
    SequenceQualityResult,
    SequenceWindowEvidence,
)


SUPPORTED_TIMEFRAMES = frozenset({"D1", "H1", "M15"})
_NORMAL_BREAK_REASONS = frozenset(
    {"WEEKEND", "HOLIDAY", "SESSION_BREAK", "MARKET_CLOSED", "OUT_OF_SESSION"}
)


def audit_candle_sequence(
    *,
    instrument: Instrument,
    timeframe: str,
    period_start: date,
    period_end: date,
    candle_series: CandleSeries | Sequence[CandleSeries] | None,
    schedule_evidence: CandleSequenceScheduleEvidence | None,
) -> SequenceQualityResult:
    """Compare a period-filtered series with explicit schedule/activity evidence.

    The caller must pass all successfully read exact-contract series for the
    requested trade-date period. For a queried contract with no bars, pass an
    empty CandleSeries; pass None when the source read itself is unavailable.
    Futures roots may be audited across rollover by supplying one series per
    historical contract. No schedule is inferred from timestamp steps.
    """
    tf = timeframe.strip().upper()
    if tf not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if period_start > period_end:
        raise ValueError("period_start must not be after period_end")

    instrument_key = _instrument_key(instrument)
    period_from = period_start.isoformat()
    period_till = period_end.isoformat()
    if candle_series is None:
        series: tuple[CandleSeries, ...] | None = None
    elif isinstance(candle_series, CandleSeries):
        series = (candle_series,)
    else:
        series = tuple(candle_series) or None

    series_by_contract: dict[str, list[CandleSeries]] = defaultdict(list)
    actual_counts: Counter[tuple[str, str, str]] = Counter()
    actual_objects: dict[tuple[str, str, str], CandleIntervalKey] = {}
    observed_count: int | None = None

    if series is not None:
        observed_count = 0
        for item in series:
            if item.timeframe.strip().upper() != tf:
                raise ValueError("candle series timeframe does not match audit timeframe")
            if not _same_instrument_family(instrument, item.instrument):
                raise ValueError(
                    "candle series instrument does not match requested instrument/root"
                )
            contract = item.instrument.secid.upper()
            series_by_contract[contract].append(item)
            observed_count += item.count
            for candle in item.candles:
                key = _observation_key(
                    tf,
                    CandleIntervalKey(
                        contract_secid=item.instrument.secid,
                        begin=candle.begin,
                        end=candle.end,
                    ),
                )
                actual_counts[key] += 1
                actual_objects[key] = CandleIntervalKey(
                    contract_secid=item.instrument.secid,
                    begin=candle.begin,
                    end=candle.end,
                )

    evidence = schedule_evidence
    if evidence is not None:
        if evidence.timeframe.strip().upper() != tf:
            raise ValueError("schedule evidence timeframe does not match audit timeframe")
        if evidence.instrument_key.strip().upper() != instrument_key:
            raise ValueError("schedule evidence instrument does not match audit instrument")
        in_period = tuple(
            window
            for window in evidence.windows
            if _trade_date(window.trade_date) >= period_start
            and _trade_date(window.trade_date) <= period_end
        )
        coverage_status = evidence.coverage_status
        schedule_source = evidence.source
        schedule_version = evidence.version
        schedule_timezone = evidence.timezone
    else:
        in_period = ()
        coverage_status = "UNKNOWN"
        schedule_source = None
        schedule_version = None
        schedule_timezone = None

    reasons: set[str] = set()
    unverified: list[SequenceWindowEvidence] = []
    valid_windows: list[SequenceWindowEvidence] = []
    window_groups: dict[tuple[str, str, str], list[SequenceWindowEvidence]] = defaultdict(list)

    if evidence is None:
        reasons.add("SCHEDULE_EVIDENCE_UNAVAILABLE")
    else:
        if not _schedule_covers_period(evidence, period_start, period_end):
            reasons.add("SCHEDULE_COVERAGE_NOT_PROVEN")
        if not evidence.source or not evidence.version or not evidence.timezone:
            reasons.add("SCHEDULE_PROVENANCE_INCOMPLETE")
        for window in in_period:
            key = _evidence_key(window, tf)
            window_groups[key].append(window)

    for key, grouped in window_groups.items():
        if len(grouped) != 1:
            unverified.extend(grouped)
            reasons.add("CONFLICTING_SCHEDULE_EVIDENCE")
            continue
        window = grouped[0]
        if window.disposition == "UNVERIFIED":
            unverified.append(window)
            reasons.add("SCHEDULE_WINDOW_UNVERIFIED")
            continue
        if not window.source_ref or not window.rule_version:
            unverified.append(window)
            reasons.add("SCHEDULE_WINDOW_PROVENANCE_INCOMPLETE")
            continue
        valid_windows.append(window)

    required = tuple(
        window for window in valid_windows if window.disposition == "BAR_REQUIRED"
    )
    normal_breaks = tuple(
        sorted(
            (
                window
                for window in valid_windows
                if window.disposition == "NO_BAR_EXPECTED"
                and window.reason.strip().upper() in _NORMAL_BREAK_REASONS
            ),
            key=_window_sort_key,
        )
    )
    no_trade_intervals = tuple(
        sorted(
            (
                window
                for window in valid_windows
                if window.disposition == "NO_BAR_EXPECTED"
                and window.reason.strip().upper() == "NO_TRADES"
            ),
            key=_window_sort_key,
        )
    )
    other_no_bar_intervals = tuple(
        sorted(
            (
                window
                for window in valid_windows
                if window.disposition == "NO_BAR_EXPECTED"
                and window.reason.strip().upper() not in _NORMAL_BREAK_REASONS
                and window.reason.strip().upper() != "NO_TRADES"
            ),
            key=_window_sort_key,
        )
    )
    evidence_contracts = {
        window.interval.contract_secid.upper() for window in valid_windows
    }
    unavailable_contracts = (
        evidence_contracts - set(series_by_contract)
        if series is not None
        else evidence_contracts
    )
    if series is None:
        reasons.add("CANDLE_SOURCE_UNAVAILABLE")
    elif unavailable_contracts:
        reasons.add("CANDLE_SERIES_MISSING_FOR_CONTRACT")

    missing: list[SequenceWindowEvidence] = []
    matched_count: int | None = None if series is None else 0
    observed_schedule_keys = {_evidence_key(window, tf) for window in valid_windows}
    expected_keys = {_evidence_key(window, tf) for window in required}
    no_bar_keys = {
        _evidence_key(window, tf)
        for window in valid_windows
        if window.disposition == "NO_BAR_EXPECTED"
    }
    unverified_keys = {_evidence_key(window, tf) for window in unverified}

    if series is not None:
        for window in required:
            key = _evidence_key(window, tf)
            contract = window.interval.contract_secid.upper()
            if contract not in series_by_contract:
                continue
            count = actual_counts.get(key, 0)
            if count:
                assert matched_count is not None
                matched_count += 1
            else:
                missing.append(window)

    unexpected = tuple(
        actual_objects[key]
        for key in sorted(actual_counts)
        if actual_counts[key] and key in no_bar_keys
    )
    unclassified = tuple(
        actual_objects[key]
        for key in sorted(actual_counts)
        if actual_counts[key]
        and key not in observed_schedule_keys
        and key not in unverified_keys
    )
    duplicates = tuple(
        actual_objects[key]
        for key in sorted(actual_counts)
        if actual_counts[key] > 1
    )

    if unexpected:
        reasons.add("CANDLE_IN_PROVEN_NO_BAR_WINDOW")
    if unclassified:
        reasons.add("OBSERVED_CANDLE_WITHOUT_SCHEDULE_CLASSIFICATION")
    if duplicates:
        reasons.add("DUPLICATE_CANDLE_INTERVAL")

    schedule_complete = (
        evidence is not None
        and _schedule_covers_period(evidence, period_start, period_end)
        and bool(evidence.source and evidence.version and evidence.timezone)
        and not unverified
        and not reasons.intersection(
            {
                "SCHEDULE_EVIDENCE_UNAVAILABLE",
                "SCHEDULE_COVERAGE_NOT_PROVEN",
                "SCHEDULE_PROVENANCE_INCOMPLETE",
                "CONFLICTING_SCHEDULE_EVIDENCE",
                "SCHEDULE_WINDOW_UNVERIFIED",
                "SCHEDULE_WINDOW_PROVENANCE_INCOMPLETE",
            }
        )
    )
    expected_count = len(required) if schedule_complete else None
    proven_expected_count = len(required)

    has_confirmed_defect = bool(missing or unexpected or duplicates)
    has_uncertainty = (
        not schedule_complete
        or series is None
        or bool(unavailable_contracts)
        or bool(unverified)
        or bool(unclassified)
    )
    if has_confirmed_defect:
        status = "DEGRADED"
        if missing:
            reasons.add("MISSING_PROVEN_EXPECTED_CANDLE")
    elif has_uncertainty:
        status = "UNVERIFIED"
    else:
        status = "PASS"

    return SequenceQualityResult(
        contract_version=CANDLE_SEQUENCE_QUALITY_VERSION,
        status=status,
        instrument_key=instrument_key,
        timeframe=tf,
        period_start=period_from,
        period_end=period_till,
        schedule_source=schedule_source,
        schedule_version=schedule_version,
        schedule_timezone=schedule_timezone,
        schedule_coverage_status=coverage_status,
        expected_count=expected_count,
        proven_expected_count=proven_expected_count,
        observed_count=observed_count,
        unavailable_contracts=tuple(sorted(unavailable_contracts)),
        matched_count=matched_count,
        missing_intervals=tuple(sorted(missing, key=_window_sort_key)),
        normal_breaks=normal_breaks,
        no_trade_intervals=no_trade_intervals,
        other_no_bar_intervals=other_no_bar_intervals,
        unverified_intervals=tuple(sorted(unverified, key=_window_sort_key)),
        unexpected_observations=unexpected,
        unclassified_observations=unclassified,
        duplicate_observations=duplicates,
        reasons=tuple(sorted(reasons)),
    )


def _instrument_key(instrument: Instrument) -> str:
    identity = instrument.root_symbol if instrument.asset_class == "future" else None
    return (identity or instrument.symbol).strip().upper()


def _same_instrument_family(requested: Instrument, observed: Instrument) -> bool:
    if requested.asset_class == "future":
        if observed.asset_class != "future":
            return False
        return (
            _instrument_key(requested) == _instrument_key(observed)
            and requested.engine.casefold() == observed.engine.casefold()
            and requested.market.casefold() == observed.market.casefold()
            and requested.board.casefold() == observed.board.casefold()
        )
    return (
        requested.secid.casefold() == observed.secid.casefold()
        and requested.engine.casefold() == observed.engine.casefold()
        and requested.market.casefold() == observed.market.casefold()
        and requested.board.casefold() == observed.board.casefold()
    )


def _trade_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid evidence trade_date: {value}") from exc


def _schedule_covers_period(
    evidence: CandleSequenceScheduleEvidence,
    start: date,
    end: date,
) -> bool:
    if evidence.coverage_status != "COMPLETE":
        return False
    if not evidence.coverage_from or not evidence.coverage_till:
        return False
    try:
        coverage_from = date.fromisoformat(evidence.coverage_from)
        coverage_till = date.fromisoformat(evidence.coverage_till)
    except ValueError:
        return False
    return coverage_from <= start and coverage_till >= end


def _evidence_key(
    window: SequenceWindowEvidence, timeframe: str
) -> tuple[str, str, str]:
    return _interval_key(timeframe, window.interval)


def _observation_key(
    timeframe: str, interval: CandleIntervalKey
) -> tuple[str, str, str]:
    return _interval_key(timeframe, interval)


def _interval_key(
    timeframe: str, interval: CandleIntervalKey
) -> tuple[str, str, str]:
    """Return the comparison identity for one provider interval.

    MOEX D1 rows use the actual last-trade timestamp as end. That value is
    not a stable schedule boundary and may differ across sessions, so D1
    completeness is keyed by contract and begin date. Intraday bars retain the
    exact begin/end identity required by their session evidence.
    """
    return (
        interval.contract_secid.upper(),
        interval.begin[:10] if timeframe == "D1" else interval.begin,
        "" if timeframe == "D1" else interval.end,
    )


def _window_sort_key(
    window: SequenceWindowEvidence,
) -> tuple[str, str, str, str]:
    return (
        window.trade_date,
        window.interval.contract_secid.upper(),
        window.interval.begin,
        window.interval.end,
    )