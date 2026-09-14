"""Causal historical walk-forward validation of the current Forecast Engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean

from birzha.application.forecast import ForecastService, build_forecast_from_snapshot
from birzha.application.historical_data import HistoricalDataService
from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.application.outcome import OutcomeService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.stored_market_data import StoredMarketDataView
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.forecast import ForecastRecord
from birzha.domain.outcome import HorizonOutcome
from birzha.domain.snapshot import MarketSnapshot
from birzha.domain.validation import HorizonValidationMetrics, WalkForwardReport
from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.providers.moex_iss import MoexIssError
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal


MANDATORY_PRICE_TIMEFRAMES = ("D1", "H1", "M15")
MINIMUM_PRICE_CANDLES = 50
VALIDATION_HORIZONS = (5, 10, 20)


@dataclass(slots=True)
class WalkForwardValidator:
    market_data: MarketDataService
    forecasts: ForecastService
    calendar: MoexTradingCalendar
    history: HistoricalDataService | None = None
    historical_flow: HistoricalFlowDataService | None = None
    prepare_history_before_run: bool = True

    @classmethod
    def default(cls) -> "WalkForwardValidator":
        shared_control = ProcessUpstreamControlPlane()
        market_data = MarketDataService.default(control_plane=shared_control)
        return cls(
            market_data=market_data,
            forecasts=ForecastService.default(control_plane=shared_control),
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
        if max_points <= 0 or max_points > 240:
            raise ValueError("max_points must be between 1 and 240")

        forecast_service = self.forecasts
        outcome_market = self.market_data
        if self.history is not None:
            if self.prepare_history_before_run:
                self._prepare_history(symbol, start=start, end=end)
            stored = StoredMarketDataView(
                self.market_data,
                self.history,
                require_stored_resolution=not self.prepare_history_before_run,
            )
            forecast_service = ForecastService(
                MarketSnapshotService(
                    market_data=stored,
                    flow=self.forecasts.snapshots.flow,
                ),
                parameters=self.forecasts.parameters,
            )
            outcome_market = stored

        sessions = self._validation_sessions(symbol, start=start, end=end)
        eligible = sessions[:-20] if len(sessions) > 20 else ()
        candidates = tuple(eligible[::step_sessions])

        forecast_store = DuckDBForecastJournal(":memory:")
        outcome_store = DuckDBOutcomeJournal(":memory:")
        outcome_service = OutcomeService(
            market_data=outcome_market,
            forecasts=forecast_store,
            outcomes=outcome_store,
        )
        pairs: list[tuple[ForecastRecord, HorizonOutcome]] = []
        failures: list[str] = []
        versions: set[str] = set()
        completed = 0
        attempted = 0
        try:
            for forecast_day in candidates:
                if completed >= max_points:
                    break
                attempted += 1
                try:
                    snapshot = forecast_service.snapshots.build(
                        symbol,
                        as_of_date=forecast_day.isoformat(),
                    )
                    if not _mandatory_price_quality_pass(snapshot):
                        failures.append(
                            f"{forecast_day.isoformat()}:mandatory_price_quality_incomplete"
                        )
                        continue
                    record = build_forecast_from_snapshot(
                        snapshot,
                        parameters=forecast_service.parameters,
                    )
                    forecast_store.append(record)
                    versions.add(record.engine_version)
                    evaluation = outcome_service.evaluate(
                        record.forecast_id, evaluation_date=end.isoformat()
                    )
                    by_horizon = {
                        item.horizon_sessions: item for item in evaluation.outcomes
                    }
                    if any(h.sessions not in by_horizon for h in record.horizons):
                        failures.append(
                            f"{forecast_day.isoformat()}:incomplete_outcome"
                        )
                        continue
                    completed += 1
                    for horizon in record.horizons:
                        pairs.append((record, by_horizon[horizon.sessions]))
                except Exception as exc:
                    failures.append(
                        f"{forecast_day.isoformat()}:{type(exc).__name__}:{exc}"
                    )
        finally:
            forecast_store.close()
            outcome_store.close()

        metrics = summarize_walk_forward(pairs, step_sessions=step_sessions)
        if not candidates:
            status = "NO_ELIGIBLE_POINTS"
        elif completed == 0:
            status = "FAILED"
        elif completed < max_points:
            status = "PARTIAL"
        else:
            status = "COMPUTED"
        return WalkForwardReport(
            symbol=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            requested_points=attempted,
            completed_forecasts=completed,
            failed_forecasts=len(failures),
            engine_versions=tuple(sorted(versions)),
            metrics=metrics,
            failures=tuple(failures[:100]),
            status=status,
            step_sessions=step_sessions,
        )

    def _prepare_history(self, symbol: str, *, start: date, end: date) -> None:
        assert self.history is not None
        requests = (
            ("D1", start - timedelta(days=300)),
            ("H1", start - timedelta(days=90)),
            ("M15", start - timedelta(days=30)),
        )
        for timeframe, left in requests:
            self.history.sync(
                symbol,
                timeframe=timeframe,
                from_date=left.isoformat(),
                till_date=end.isoformat(),
            )
        if self.historical_flow is not None:
            self.historical_flow.sync(
                symbol,
                from_date=(start - timedelta(days=10)).isoformat(),
                till_date=end.isoformat(),
            )

    def _validation_sessions(
        self, symbol: str, *, start: date, end: date
    ) -> tuple[date, ...]:
        if self.history is not None:
            return self.history.session_dates(
                symbol,
                from_date=start.isoformat(),
                till_date=end.isoformat(),
            )
        return self._session_dates(symbol, start=start, end=end)

    def _session_dates(
        self, symbol: str, *, start: date, end: date
    ) -> tuple[date, ...]:
        """Build a real-session calendar from exact securities, including rolls."""

        resolver = self.market_data.direct_resolver
        if resolver is not None and not is_futures_root_symbol(symbol):
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
            raise RuntimeError(
                "historical futures resolver is required for walk-forward validation"
            )

        sessions: set[date] = set()
        cursor = start
        seen_contracts: set[str] = set()
        while cursor <= end:
            try:
                instrument, resolved_day = self._resolve_future_on_or_after(
                    symbol, cursor, end
                )
            except MoexIssError:
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
            f"No historical MOEX futures session found for {symbol!r} "
            f"on or after {start.isoformat()}"
        )


def _mandatory_price_quality_pass(snapshot: MarketSnapshot) -> bool:
    """Require complete D1/H1/M15 price inputs while allowing optional flow gaps."""
    contract = snapshot.quality_contract
    if contract is not None:
        by_timeframe = {item.timeframe: item for item in contract.timeframes}
        return all(
            timeframe in by_timeframe
            and by_timeframe[timeframe].status == "PASS"
            and by_timeframe[timeframe].candles
            >= by_timeframe[timeframe].minimum_required
            for timeframe in MANDATORY_PRICE_TIMEFRAMES
        )
    return all(
        state.candles >= MINIMUM_PRICE_CANDLES
        for state in (snapshot.d1, snapshot.h1, snapshot.m15)
    )


def independent_sample_capacity(
    total_sessions: int,
    *,
    step_sessions: int,
    max_points: int,
    horizons: tuple[int, ...] = VALIDATION_HORIZONS,
) -> dict[int, int]:
    """Upper bound on non-overlapping observations available before a run.

    The longest forecast horizon must mature inside the period, matching the
    walk-forward rule that excludes the final horizon-length sessions. The
    result is deliberately an upper bound: data-quality failures or contract
    expiry can only reduce the realized sample further.
    """
    if total_sessions < 0:
        raise ValueError("total_sessions must be >= 0")
    if step_sessions <= 0:
        raise ValueError("step_sessions must be > 0")
    if max_points <= 0:
        raise ValueError("max_points must be > 0")
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("horizons must contain positive session counts")

    longest = max(horizons)
    eligible_sessions = max(0, total_sessions - longest)
    raw_candidates = min(
        max_points,
        (eligible_sessions + step_sessions - 1) // step_sessions,
    )
    return {
        horizon: (
            raw_candidates
            + max(1, (horizon + step_sessions - 1) // step_sessions)
            - 1
        )
        // max(1, (horizon + step_sessions - 1) // step_sessions)
        for horizon in horizons
    }


def summarize_walk_forward(
    pairs: list[tuple[ForecastRecord, HorizonOutcome]],
    *,
    step_sessions: int = 1,
) -> tuple[HorizonValidationMetrics, ...]:
    """Summarize only non-overlapping forecast windows for each horizon.

    Forecast T0s are generated every ``step_sessions`` exchange sessions. For a
    horizon ``h`` we therefore keep every ceil(h / step_sessions)-th successful
    forecast. Failed/missing forecasts can only increase the actual separation,
    so this deterministic thinning never creates overlap that was not already
    present. It removes label overlap; it does not claim that market observations
    are otherwise statistically independent.
    """
    if step_sessions <= 0:
        raise ValueError("step_sessions must be > 0")

    result: list[HorizonValidationMetrics] = []
    horizons = sorted({outcome.horizon_sessions for _, outcome in pairs})
    for sessions in horizons:
        raw_subset = sorted(
            (
                (forecast, outcome)
                for forecast, outcome in pairs
                if outcome.horizon_sessions == sessions
            ),
            key=lambda item: item[0].created_at_t0,
        )
        sampling_stride = max(1, (sessions + step_sessions - 1) // step_sessions)
        subset = raw_subset[::sampling_stride]
        directional = [
            outcome for _, outcome in subset if outcome.direction_hit is not None
        ]
        hits = sum(1 for outcome in directional if outcome.direction_hit)
        actuals = [outcome.actual_return_pct for _, outcome in subset]
        errors: list[float] = []
        for forecast, outcome in subset:
            expected = next(
                (
                    item.expected_move_pct
                    for item in forecast.horizons
                    if item.sessions == sessions
                ),
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
                direction_hit_rate=(
                    round(hits / len(directional), 6) if directional else None
                ),
                mean_actual_return_pct=(
                    round(fmean(actuals), 6) if actuals else None
                ),
                mean_absolute_error_pct=(
                    round(fmean(abs(item) for item in errors), 6)
                    if errors
                    else None
                ),
                mean_signed_error_pct=(
                    round(fmean(errors), 6) if errors else None
                ),
                raw_observations=len(raw_subset),
                sampling_stride=sampling_stride,
            )
        )
    return tuple(result)
