"""Resumable bounded-window orchestration for durable historical backfills."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from birzha.application.historical_data import HistoricalDataService


@dataclass(frozen=True, slots=True)
class BackfillWindow:
    from_date: str
    till_date: str

    def to_dict(self) -> dict[str, str]:
        return {"from_date": self.from_date, "till_date": self.till_date}


@dataclass(frozen=True, slots=True)
class BackfillRunResult:
    status: str
    complete: bool
    processed_windows: int
    next_from_date: str | None
    windows: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "complete": self.complete,
            "processed_windows": self.processed_windows,
            "next_from_date": self.next_from_date,
            "windows": list(self.windows),
        }


@dataclass(slots=True)
class HistoricalBackfillService:
    history: HistoricalDataService

    def run(
        self,
        symbols: list[str],
        timeframes: list[str],
        *,
        from_date: str,
        till_date: str,
        max_windows: int = 1,
    ) -> BackfillRunResult:
        if max_windows <= 0 or max_windows > 24:
            raise ValueError("max_windows must be between 1 and 24")
        windows = monthly_windows(from_date, till_date)
        selected = windows[:max_windows]
        items: list[dict[str, object]] = []
        for window in selected:
            batch = self.history.sync_many(
                symbols,
                timeframes,
                from_date=window.from_date,
                till_date=window.till_date,
            )
            items.append({"window": window.to_dict(), "batch": batch})
            if batch["status"] != "PASS":
                return BackfillRunResult(
                    status="PARTIAL",
                    complete=False,
                    processed_windows=len(items),
                    next_from_date=window.from_date,
                    windows=tuple(items),
                )
        complete = len(selected) == len(windows)
        next_from = None if complete else windows[len(selected)].from_date
        return BackfillRunResult("PASS", complete, len(selected), next_from, tuple(items))


def monthly_windows(from_date: str, till_date: str) -> tuple[BackfillWindow, ...]:
    start = date.fromisoformat(from_date[:10])
    finish = date.fromisoformat(till_date[:10])
    if finish < start:
        raise ValueError("till_date must be on or after from_date")
    result: list[BackfillWindow] = []
    cursor = start
    while cursor <= finish:
        month_end = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        right = min(month_end, finish)
        result.append(BackfillWindow(cursor.isoformat(), right.isoformat()))
        cursor = date(right.year + 1, 1, 1) if right.month == 12 else date(right.year, right.month + 1, 1)
    return tuple(result)
