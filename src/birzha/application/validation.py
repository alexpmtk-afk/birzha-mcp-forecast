"""Causal historical walk-forward validation of the current Forecast Engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean

from birzha.application.forecast import ForecastService
from birzha.application.market_data import MarketDataService
from birzha.application.outcome import OutcomeService
from birzha.domain.forecast import ForecastRecord
from birzha.domain.outcome import HorizonOutcome
from birzha.domain.validation import HorizonValidationMetrics, WalkForwardReport
from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.providers.moex_iss import MoexIssError
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal


@dataclass(slots=True)
class WalkForwardValidator:
    market_data: MarketDataService
    forecasts: ForecastService
    calendar: MoexTradingCalendar

    @classmethod
    def default(cls) -> "WalkForwardValidator":
        market_data = MarketDataService.default()
        return cls(
            market_data=market_data,
            forecasts=ForecastService.default(),
            calendar=MoexTradingCalendar(market_data.provider),
        )

    def run(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        step_sessions: int = 5,
        max_points: int = 24,
    ) -> WalkForwardReport:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start >= end:
            raise ValueError("start_date must be before end_date")
        if step_sessions <= 0 or step_sessions > 50:
            raise ValueError("step_sessions must be between 1 and 50")
        if max_points <= 0 or max_points > 60:
            raise ValueError("max_points must be between 1 and 60")

        sessions = self._session_dates(symbol, start=start, end=end)
        eligible = sessions[:-20] if len(sessions) > 20 else ()
        selected = tuple(eligible[::step_sessions][:max_points])

        forecast_store = DuckDBForecastJournal(":memory:")
        outcome_store = DuckDBOutcomeJournal(":memory:")
        outcome_service = OutcomeService(
            market_data=self.market_data,
            forecasts=forecast_store,
            outcomes=outcome_store,
        )
        pairs: list[tuple[ForecastRecord, HorizonOutcome]] = []
        failures: list[str] = []
        versions: set[str] = set()
        completed = 0
        try:
            for forecast_day in selected:
                try:
                    record = self.forecasts.build(symbol, as_of_date=forecast_day.isoformat())
                    forecast_store.append(record)
                    versions.add(record.engine_version)
                    evaluation = outcome_service.evaluate(
                        record.forecast_id, evaluation_date=end.isoformat()
                    )
                    by_horizon = {item.horizon_sessions: item for item in evaluation.outcomes}
                    if any(h.sessions not in by_horizon for h in record.horizons):
                        failures.append(f"{forecast_day.isoformat()}:incomplete_outcome")
                        continue
                    completed += 1
                    for horizon in record.horizons:
                        pairs.append((record, by_horizon[horizon.sessions]))
                except Exception as exc:
                    failures.append(f"{forecast_day.isoformat()}:{type(exc).__name__}:{exc}")
        finally:
            forecast_store.close()
            outcome_store.close()

        metrics = summarize_walk_forward(pairs)
        if not selected:
            status = "NO_ELIGIBLE_POINTS"
        elif completed == 0:
            status = "FAILED"
        elif failures:
            status = "PARTIAL"
        else:
            status = "COMPUTED"
        return WalkForwardReport(
            symbol=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            requested_points=len(selected),
            completed_forecasts=completed,
            failed_forecasts=len(failures),
            engine_versions=tuple(sorted(versions)),
            metrics=metrics,
            failures=tuple(failures[:100]),
            status=status,
        )

    def _session_dates(self, symbol: str, *, start: date, end: date) -> tuple[date, ...]:
        """Build a real-session calendar from exact securities, including rolls."""

        resolver = self.market_data.direct_resolver
        if resolver is not None:
            direct = resolver.resolve(symbol)
            if direct is not None and direct.asset_class != "unknown":
                return self.calendar.dates(
                    engine=direct.engine,
                    market=direct.market,
                    board=direct.board,
                    security=direct.secid,
                    from_date=start,
                    till_date=end,
                )

        historical = self.market_data.historical_future_resolver
        if historical is None:
            raise RuntimeError("historical futures resolver is required for walk-forward validation")

        sessions: set[date] = set()
        cursor = start
        seen_contracts: set[str] = set()
        while cursor <= end:
            try:
                instrument, resolved_day = self._resolve_future_on_or_after(symbol, cursor, end)
            except MoexIssError:
                # If we already collected valid sessions, reaching a trailing
                # weekend/holiday after the last actual session is normal and
                # must terminate the calendar rather than fail the whole run.
                if sessions:
                    break
                raise
            if instrument.secid in seen_contracts:
                cursor = resolved_day + timedelta(days=1)
                continue
            seen_contracts.add(instrument.secid)
            contract_dates = self.calendar.dates(
                engine=instrument.engine,
                market=instrument.market,
                board=instrument.board,
                security=instrument.secid,
                from_date=resolved_day,
                till_date=end,
            )
            relevant = tuple(day for day in contract_dates if day >= cursor)
            if not relevant:
                cursor = resolved_day + timedelta(days=1)
                continue
            sessions.update(relevant)
            last = relevant[-1]
            if last >= end:
                break
            cursor = last + timedelta(days=1)

        return tuple(sorted(day for day in sessions if start <= day <= end))

    def _resolve_future_on_or_after(
        self, symbol: str, start: date, end: date
    ) -> tuple[object, date]:
        historical = self.market_data.historical_future_resolver
        assert historical is not None
        probe = start
        for _ in range(14):
            if probe > end:
                break
            if probe.weekday() < 5:
                try:
                    return historical.resolve(symbol, probe), probe
                except MoexIssError:
                    pass
            probe += timedelta(days=1)
        raise MoexIssError(
            f"No historical MOEX futures session found for {symbol!r} on or after {start.isoformat()}"
        )


def summarize_walk_forward(
    pairs: list[tuple[ForecastRecord, HorizonOutcome]],
) -> tuple[HorizonValidationMetrics, ...]:
    result: list[HorizonValidationMetrics] = []
    horizons = sorted({outcome.horizon_sessions for _, outcome in pairs})
    for sessions in horizons:
        subset = [(forecast, outcome) for forecast, outcome in pairs if outcome.horizon_sessions == sessions]
        directional = [outcome for _, outcome in subset if outcome.direction_hit is not None]
        hits = sum(1 for outcome in directional if outcome.direction_hit)
        actuals = [outcome.actual_return_pct for _, outcome in subset]
        errors: list[float] = []
        for forecast, outcome in subset:
            expected = next(
                (item.expected_move_pct for item in forecast.horizons if item.sessions == sessions),
                None,
            )
            if expected is not None:
                errors.append(outcome.actual_return_pct - expected)
        result.append(
            HorizonValidationMetrics(
                sessions=sessions,
                observations=len(subset),
                directional_observations=len(directional),
                direction_hits=hits,
                direction_hit_rate=round(hits / len(directional), 6) if directional else None,
                mean_actual_return_pct=round(fmean(actuals), 6) if actuals else None,
                mean_absolute_error_pct=round(fmean(abs(item) for item in errors), 6) if errors else None,
                mean_signed_error_pct=round(fmean(errors), 6) if errors else None,
            )
        )
    return tuple(result)
