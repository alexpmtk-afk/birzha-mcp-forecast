"""Evaluate immutable forecasts against future completed MOEX D1 sessions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from birzha.application.market_data import MOEX_TIMEZONE, MarketDataService
from birzha.domain.forecast import ForecastRecord
from birzha.domain.market import Instrument
from birzha.domain.outcome import HorizonOutcome, OutcomeEvaluation
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal


@dataclass(slots=True)
class OutcomeService:
    market_data: MarketDataService
    forecasts: DuckDBForecastJournal
    outcomes: DuckDBOutcomeJournal

    def evaluate(self, forecast_id: str, *, evaluation_date: str | None = None) -> OutcomeEvaluation:
        forecast = self.forecasts.get(forecast_id)
        if forecast is None:
            raise KeyError(f"forecast not found: {forecast_id}")
        t0 = _parse_time(forecast.created_at_t0)
        instrument = self._exact_instrument(forecast, t0=t0)
        start_date = (t0.date() - timedelta(days=7)).isoformat()
        cutoff = date.fromisoformat(evaluation_date) if evaluation_date else datetime.now(MOEX_TIMEZONE).date()
        if cutoff < t0.date():
            raise ValueError("evaluation_date must not be before forecast T0")
        till_date = (cutoff + timedelta(days=1)).isoformat()
        series = self.market_data.candles_for_instrument(
            instrument,
            timeframe="D1",
            from_date=start_date,
            till_date=till_date,
            completed_only=True,
        )
        before = [c for c in series.candles if _parse_time(c.end) <= t0 and c.close is not None]
        future = [
            c for c in series.candles
            if _parse_time(c.begin).date() > t0.date()
            and _parse_time(c.begin).date() <= cutoff
            and c.close is not None
        ]
        reference = forecast.reference_price
        if reference is None:
            reference = before[-1].close if before else None
        if reference in {None, 0.0}:
            raise ValueError("forecast has no valid causal reference price")

        existing = {item.horizon_sessions: item for item in self.outcomes.list_for_forecast(forecast_id)}
        observed: list[HorizonOutcome] = []
        pending: list[int] = []
        for horizon in sorted(item.sessions for item in forecast.horizons):
            if horizon in existing:
                observed.append(existing[horizon])
                continue
            if len(future) < horizon:
                pending.append(horizon)
                continue
            window = future[:horizon]
            target = window[-1]
            assert target.close is not None
            actual_return = (target.close / reference - 1.0) * 100.0
            highs = [c.high for c in window if c.high is not None]
            lows = [c.low for c in window if c.low is not None]
            best_up = ((max(highs) / reference) - 1.0) * 100.0 if highs else None
            best_down = ((min(lows) / reference) - 1.0) * 100.0 if lows else None
            if forecast.direction == "UP":
                hit = actual_return > 0
                mfe, mae = best_up, best_down
            elif forecast.direction == "DOWN":
                hit = actual_return < 0
                mfe = -best_down if best_down is not None else None
                mae = -best_up if best_up is not None else None
            else:
                hit = None
                mfe, mae = None, None
            outcome_id = "out_" + hashlib.sha256(f"{forecast_id}:{horizon}".encode()).hexdigest()[:24]
            record = HorizonOutcome(
                outcome_id=outcome_id,
                forecast_id=forecast_id,
                symbol=forecast.symbol,
                secid=forecast.secid,
                horizon_sessions=horizon,
                reference_price=float(reference),
                target_session_end=target.end,
                target_close=float(target.close),
                actual_return_pct=round(actual_return, 6),
                direction_hit=hit,
                max_favorable_excursion_pct=round(mfe, 6) if mfe is not None else None,
                max_adverse_excursion_pct=round(mae, 6) if mae is not None else None,
            )
            self.outcomes.append(record)
            observed.append(record)
        status = "COMPLETE" if not pending else "PARTIAL" if observed else "PENDING"
        return OutcomeEvaluation(
            forecast_id=forecast.forecast_id,
            symbol=forecast.symbol,
            secid=forecast.secid,
            available_future_sessions=len(future),
            outcomes=tuple(sorted(observed, key=lambda item: item.horizon_sessions)),
            pending_horizons=tuple(pending),
            status=status,
        )

    def _exact_instrument(self, forecast: ForecastRecord, *, t0: datetime) -> Instrument:
        """Recover the immutable instrument as it existed at forecast T0.

        A history-backed validation view may expose exact instrument metadata
        directly from the prepared store. That path is preferred so model
        evaluation does not re-resolve historical contracts against live MOEX.
        Outside a stored view, current/historical provider resolution remains the
        compatibility fallback and must still reproduce the immutable SECID.
        """

        stored_lookup = getattr(self.market_data, "stored_instrument", None)
        if callable(stored_lookup):
            instrument = stored_lookup(forecast.secid)
            if instrument is not None:
                if instrument.secid != forecast.secid:
                    raise ValueError(
                        "stored instrument returned a different SECID than the Forecast Record"
                    )
                if instrument.asset_class == "future":
                    root = (instrument.root_symbol or instrument.symbol).upper()
                    if root != forecast.symbol.upper():
                        raise ValueError(
                            "stored futures root does not match Forecast Record: "
                            f"stored={root}, forecast={forecast.symbol}"
                        )
                return instrument

        resolver = self.market_data.direct_resolver
        if resolver is not None:
            instrument = resolver.resolve(forecast.secid)
            if instrument is not None:
                if instrument.secid != forecast.secid:
                    raise ValueError("current resolver returned a different SECID than the Forecast Record")
                return instrument

        historical = self.market_data.historical_future_resolver
        if historical is not None:
            instrument = historical.resolve(forecast.symbol, t0.date())
            if instrument.secid != forecast.secid:
                raise ValueError(
                    "historical contract at forecast T0 does not match stored SECID: "
                    f"stored={forecast.secid}, resolved={instrument.secid}"
                )
            return instrument

        raise ValueError(f"stored SECID is no longer resolvable on MOEX: {forecast.secid}")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOEX_TIMEZONE)
    else:
        parsed = parsed.astimezone(MOEX_TIMEZONE)
    return parsed
