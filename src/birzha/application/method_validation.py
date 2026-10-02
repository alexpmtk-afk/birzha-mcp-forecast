"""Walk-forward validation for independent forecast-method signals.

This reuses the same causal snapshot/outcome machinery as the production
forecast validator, but evaluates each directional method separately.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from birzha.application.forecast import ForecastService
from birzha.application.flow import MarketFlowService
from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.method_signals import (
    METHOD_SET_VERSION,
    ForecastMethodSignal,
    build_method_signals,
)
from birzha.application.outcome import OutcomeService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.stored_market_data import StoredMarketDataView
from birzha.application.validation import (
    VALIDATION_HORIZONS,
    WalkForwardValidator,
    _mandatory_price_quality_pass,
    summarize_walk_forward,
)
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.domain.outcome import HorizonOutcome
from birzha.domain.snapshot import MarketSnapshot
from birzha.domain.validation import WalkForwardReport
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal


DIRECTIONAL_METHODS = (
    "TREND_MOMENTUM",
    "TIMEFRAME_ALIGNMENT",
    "VOLUME_LEVELS",
    "FLOW_OI",
)


@dataclass(frozen=True, slots=True)
class MethodValidationItem:
    method: str
    available_points: int
    unavailable_points: int
    report: WalkForwardReport

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "available_points": self.available_points,
            "unavailable_points": self.unavailable_points,
            "report": self.report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MethodValidationSuite:
    symbol: str
    start_date: str
    end_date: str
    methods: tuple[MethodValidationItem, ...]
    volatility_context_counts: tuple[tuple[str, int], ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "methods": [item.to_dict() for item in self.methods],
            "volatility_context_counts": {
                key: value for key, value in self.volatility_context_counts
            },
            "status": self.status,
        }


@dataclass(slots=True)
class MethodWalkForwardValidator:
    base: WalkForwardValidator

    def run(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        step_sessions: int = 5,
        max_points: int = 60,
    ) -> MethodValidationSuite:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start >= end:
            raise ValueError("start_date must be before end_date")
        if step_sessions <= 0 or step_sessions > 50:
            raise ValueError("step_sessions must be between 1 and 50")
        if max_points <= 0 or max_points > 240:
            raise ValueError("max_points must be between 1 and 240")

        snapshot_service, outcome_market = self._services(symbol, start=start, end=end)
        sessions = self.base._validation_sessions(symbol, start=start, end=end)
        eligible = sessions[:-max(VALIDATION_HORIZONS)] if len(sessions) > max(VALIDATION_HORIZONS) else ()
        candidates = tuple(eligible[::step_sessions])[:max_points]

        pairs: dict[str, list[tuple[ForecastRecord, HorizonOutcome]]] = defaultdict(list)
        failures: dict[str, list[str]] = defaultdict(list)
        completed: Counter[str] = Counter()
        available: Counter[str] = Counter()
        unavailable: Counter[str] = Counter()
        context_counts: Counter[str] = Counter()

        forecast_store = DuckDBForecastJournal(":memory:")
        outcome_store = DuckDBOutcomeJournal(":memory:")
        outcome_service = OutcomeService(
            market_data=outcome_market,
            forecasts=forecast_store,
            outcomes=outcome_store,
        )
        try:
            for forecast_day in candidates:
                day = forecast_day.isoformat()
                try:
                    snapshot = snapshot_service.build(symbol, as_of_date=day)
                except Exception as exc:
                    message = f"{day}:snapshot:{type(exc).__name__}:{exc}"
                    for method in DIRECTIONAL_METHODS:
                        failures[method].append(message)
                    continue

                if not _mandatory_price_quality_pass(snapshot):
                    for method in DIRECTIONAL_METHODS:
                        failures[method].append(
                            f"{day}:mandatory_price_quality_incomplete"
                        )
                    continue

                signals = {item.name: item for item in build_method_signals(snapshot)}
                volatility = signals.get("VOLATILITY_REGIME")
                if volatility is not None and volatility.context_state is not None:
                    context_counts[volatility.context_state] += 1

                for method in DIRECTIONAL_METHODS:
                    signal = signals[method]
                    if not signal.available or signal.direction == "UNAVAILABLE":
                        unavailable[method] += 1
                        continue
                    available[method] += 1
                    try:
                        record = build_method_forecast_record(snapshot, signal)
                        forecast_store.append(record)
                        evaluation = outcome_service.evaluate(
                            record.forecast_id,
                            evaluation_date=end.isoformat(),
                        )
                        by_horizon = {
                            item.horizon_sessions: item
                            for item in evaluation.outcomes
                        }
                        if any(
                            horizon not in by_horizon
                            for horizon in VALIDATION_HORIZONS
                        ):
                            failures[method].append(f"{day}:incomplete_outcome")
                            continue
                        completed[method] += 1
                        for horizon in VALIDATION_HORIZONS:
                            pairs[method].append((record, by_horizon[horizon]))
                    except Exception as exc:
                        failures[method].append(
                            f"{day}:{type(exc).__name__}:{exc}"
                        )
        finally:
            forecast_store.close()
            outcome_store.close()

        items: list[MethodValidationItem] = []
        for method in DIRECTIONAL_METHODS:
            metrics = summarize_walk_forward(
                pairs[method],
                step_sessions=step_sessions,
            )
            report = WalkForwardReport(
                symbol=symbol,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                requested_points=len(candidates),
                completed_forecasts=completed[method],
                failed_forecasts=len(failures[method]),
                engine_versions=(f"{METHOD_SET_VERSION}:{method}",),
                metrics=metrics,
                failures=tuple(failures[method][:100]),
                status=_method_status(
                    requested=len(candidates),
                    completed=completed[method],
                    unavailable=unavailable[method],
                    failures=len(failures[method]),
                ),
                step_sessions=step_sessions,
            )
            items.append(
                MethodValidationItem(
                    method=method,
                    available_points=available[method],
                    unavailable_points=unavailable[method],
                    report=report,
                )
            )

        if not candidates:
            suite_status = "NO_ELIGIBLE_POINTS"
        elif any(item.report.status in {"COMPUTED", "PARTIAL"} for item in items):
            suite_status = "COMPUTED"
        elif all(item.report.status == "UNAVAILABLE" for item in items):
            suite_status = "UNAVAILABLE"
        else:
            suite_status = "FAILED"

        return MethodValidationSuite(
            symbol=symbol,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            methods=tuple(items),
            volatility_context_counts=tuple(sorted(context_counts.items())),
            status=suite_status,
        )

    def _services(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> tuple[MarketSnapshotService, object]:
        if self.base.history is None:
            raise RuntimeError(
                "independent method validation requires frozen stored history"
            )

        # Stage 18 is deliberately store-only. Never prepare/sync/fetch market or
        # flow history while evaluating methods, otherwise the validation dataset
        # can change during the test itself.
        stored = StoredMarketDataView(
            self.base.market_data,
            self.base.history,
            require_stored_resolution=True,
        )

        flow_service: MarketFlowService | None = None
        source_flow = self.base.forecasts.snapshots.flow
        if self.base.historical_flow is not None and source_flow is not None:
            read_only_flow = HistoricalFlowDataService(
                market_data=stored,  # type: ignore[arg-type]
                analytics=self.base.historical_flow.analytics,
                store=self.base.historical_flow.store,
                read_only=True,
            )
            flow_service = MarketFlowService(
                market_data=stored,  # type: ignore[arg-type]
                analytics=self.base.historical_flow.analytics,
                historical=read_only_flow,
            )

        snapshot_service = MarketSnapshotService(
            market_data=stored,  # type: ignore[arg-type]
            flow=flow_service,
        )
        return snapshot_service, stored


def build_method_forecast_record(
    snapshot: MarketSnapshot,
    signal: ForecastMethodSignal,
) -> ForecastRecord:
    if signal.role != "DIRECTIONAL" or not signal.available:
        raise ValueError("method signal is not an available directional signal")
    if signal.direction not in {"UP", "DOWN", "NEUTRAL"}:
        raise ValueError("method signal has no forecast direction")

    reference_price = (
        snapshot.m15.last_close
        if snapshot.m15.last_close is not None
        else snapshot.h1.last_close
        if snapshot.h1.last_close is not None
        else snapshot.d1.last_close
    )
    payload = (
        f"{METHOD_SET_VERSION}|{signal.name}|{snapshot.symbol}|"
        f"{snapshot.secid}|{snapshot.as_of}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    strength = 0.0 if signal.strength is None else float(signal.strength)
    horizons = tuple(
        HorizonForecast(
            sessions=sessions,
            direction=signal.direction,
            signal_strength=strength,
            expected_move_pct=None,
            adverse_move_pct=None,
        )
        for sessions in VALIDATION_HORIZONS
    )
    return ForecastRecord(
        forecast_id=f"mfcst_{digest}",
        symbol=snapshot.symbol,
        secid=snapshot.secid,
        created_at_t0=snapshot.as_of,
        engine_version=f"{METHOD_SET_VERSION}:{signal.name}",
        direction=signal.direction,
        signal_strength=strength,
        control=signal.name,
        route="METHOD_VALIDATION",
        horizons=horizons,
        reasons=signal.evidence,
        warnings=tuple(snapshot.warnings) + ("diagnostic_method_not_calibrated",),
        validation_status="UNVALIDATED_METHOD",
        reference_price=reference_price,
    )


def _method_status(
    *,
    requested: int,
    completed: int,
    unavailable: int,
    failures: int,
) -> str:
    if requested == 0:
        return "NO_ELIGIBLE_POINTS"
    if completed == requested:
        return "COMPUTED"
    if completed > 0:
        return "PARTIAL"
    if unavailable == requested and failures == 0:
        return "UNAVAILABLE"
    return "FAILED"
