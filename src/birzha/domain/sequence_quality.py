"""Versioned evidence contracts for candle-sequence completeness audits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


CANDLE_SEQUENCE_QUALITY_VERSION = "CANDLE_SEQUENCE_QUALITY_V0"

SequenceStatus = Literal["PASS", "DEGRADED", "UNVERIFIED"]
ScheduleCoverageStatus = Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
IntervalDisposition = Literal["BAR_REQUIRED", "NO_BAR_EXPECTED", "UNVERIFIED"]


@dataclass(frozen=True, slots=True)
class CandleIntervalKey:
    """Exact provider interval identity; timestamps retain source representation."""

    contract_secid: str
    begin: str
    end: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SequenceWindowEvidence:
    """Evidence for one interval or explicitly classified non-trading window.

    BAR_REQUIRED is valid only when independent evidence proves a candle must
    exist, for example confirmed trades or a documented empty-bar policy.
    A session timetable alone is not sufficient when the provider may omit
    intervals without trades.
    """

    trade_date: str
    interval: CandleIntervalKey
    disposition: IntervalDisposition
    reason: str
    source_ref: str | None = None
    rule_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_date": self.trade_date,
            "interval": self.interval.to_dict(),
            "disposition": self.disposition,
            "reason": self.reason,
            "source_ref": self.source_ref,
            "rule_version": self.rule_version,
        }


@dataclass(frozen=True, slots=True)
class CandleSequenceScheduleEvidence:
    """Versioned schedule/activity evidence supplied to the pure auditor."""

    instrument_key: str
    timeframe: str
    coverage_status: ScheduleCoverageStatus
    coverage_from: str | None
    coverage_till: str | None
    source: str | None
    version: str | None
    timezone: str | None
    windows: tuple[SequenceWindowEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument_key": self.instrument_key,
            "timeframe": self.timeframe,
            "coverage_status": self.coverage_status,
            "coverage_from": self.coverage_from,
            "coverage_till": self.coverage_till,
            "source": self.source,
            "version": self.version,
            "timezone": self.timezone,
            "windows": [item.to_dict() for item in self.windows],
        }


@dataclass(frozen=True, slots=True)
class SequenceQualityResult:
    """Deterministic result; unknown evidence is preserved as UNVERIFIED."""

    contract_version: str
    status: SequenceStatus
    instrument_key: str
    timeframe: str
    period_start: str
    period_end: str
    schedule_source: str | None
    schedule_version: str | None
    schedule_timezone: str | None
    schedule_coverage_status: ScheduleCoverageStatus
    expected_count: int | None
    proven_expected_count: int
    observed_count: int | None
    unavailable_contracts: tuple[str, ...]
    matched_count: int | None
    missing_intervals: tuple[SequenceWindowEvidence, ...]
    normal_breaks: tuple[SequenceWindowEvidence, ...]
    no_trade_intervals: tuple[SequenceWindowEvidence, ...]
    other_no_bar_intervals: tuple[SequenceWindowEvidence, ...]
    unverified_intervals: tuple[SequenceWindowEvidence, ...]
    unexpected_observations: tuple[CandleIntervalKey, ...]
    unclassified_observations: tuple[CandleIntervalKey, ...]
    duplicate_observations: tuple[CandleIntervalKey, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "status": self.status,
            "instrument_key": self.instrument_key,
            "timeframe": self.timeframe,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "schedule_source": self.schedule_source,
            "schedule_version": self.schedule_version,
            "schedule_timezone": self.schedule_timezone,
            "schedule_coverage_status": self.schedule_coverage_status,
            "expected_count": self.expected_count,
            "proven_expected_count": self.proven_expected_count,
            "observed_count": self.observed_count,
            "unavailable_contracts": list(self.unavailable_contracts),
            "matched_count": self.matched_count,
            "missing_intervals": [item.to_dict() for item in self.missing_intervals],
            "normal_breaks": [item.to_dict() for item in self.normal_breaks],
            "no_trade_intervals": [item.to_dict() for item in self.no_trade_intervals],
            "other_no_bar_intervals": [item.to_dict() for item in self.other_no_bar_intervals],
            "unverified_intervals": [item.to_dict() for item in self.unverified_intervals],
            "unexpected_observations": [item.to_dict() for item in self.unexpected_observations],
            "unclassified_observations": [item.to_dict() for item in self.unclassified_observations],
            "duplicate_observations": [item.to_dict() for item in self.duplicate_observations],
            "reasons": list(self.reasons),
        }