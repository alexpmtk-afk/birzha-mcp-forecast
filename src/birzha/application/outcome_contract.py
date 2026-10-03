"""Evaluate the Protocol 08 Prediction Contract by first barrier touch."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from birzha.application.market_data import MOEX_TIMEZONE, MarketDataService
from birzha.application.prediction import PredictionContractService
from birzha.domain.market import Candle, Instrument
from birzha.domain.outcome_contract import (
    OUTCOME_CONTRACT_VERSION,
    HorizonOutcomeContract,
    OutcomeContract,
)
from birzha.domain.prediction import PredictionContract
from birzha.providers.moex_analytics import MoexAnalyticsClient


@dataclass(frozen=True, slots=True)
class _Touch:
    outcome: str
    window_start: str | None
    window_end: str | None
    first_hit_at: str | None
    resolution: str | None
    ambiguity_reason: str | None = None


@dataclass(slots=True)
class OutcomeContractService:
    market_data: MarketDataService
    prediction_contracts: PredictionContractService
    analytics: MoexAnalyticsClient | None = None

    def evaluate(
        self,
        symbol: str,
        *,
        as_of_date: str | None = None,
        evaluation_date: str | None = None,
    ) -> OutcomeContract:
        prediction = self.prediction_contracts.build(
            symbol,
            as_of_date=as_of_date,
        )
        t0 = _parse_time(prediction.t0)
        cutoff = (
            date.fromisoformat(evaluation_date)
            if evaluation_date
            else datetime.now(MOEX_TIMEZONE).date()
        )
        if cutoff < t0.date():
            raise ValueError("evaluation_date must not be before Prediction Contract T0")

        instrument = self._exact_instrument(prediction, t0=t0)
        till_date = (cutoff + timedelta(days=1)).isoformat()
        d1 = self.market_data.candles_for_instrument(
            instrument,
            timeframe="D1",
            from_date=t0.date().isoformat(),
            till_date=till_date,
            completed_only=True,
        )
        future_sessions = tuple(
            candle
            for candle in d1.candles
            if _parse_time(candle.begin).date() > t0.date()
            and _parse_time(candle.begin).date() <= cutoff
        )

        matured = [
            horizon
            for horizon in prediction.horizons_sessions
            if len(future_sessions) >= horizon
        ]
        m15_candles: tuple[Candle, ...] = ()
        if matured:
            first_day = _parse_time(future_sessions[0].begin).date()
            last_day = _parse_time(future_sessions[max(matured) - 1].begin).date()
            m15_candles = self.market_data.candles_for_instrument(
                instrument,
                timeframe="M15",
                from_date=first_day.isoformat(),
                till_date=(last_day + timedelta(days=1)).isoformat(),
                completed_only=True,
            ).candles

        horizons = tuple(
            self._evaluate_horizon(
                prediction,
                instrument,
                t0,
                future_sessions,
                m15_candles,
                horizon,
            )
            for horizon in prediction.horizons_sessions
        )
        observed = [item for item in horizons if item.status == "OBSERVED"]
        data_incomplete = [item for item in horizons if item.status == "DATA_INCOMPLETE"]
        if len(observed) == len(horizons):
            status = "COMPLETE"
        elif observed or data_incomplete:
            status = "PARTIAL"
        else:
            status = "PENDING"

        identity = (
            f"{OUTCOME_CONTRACT_VERSION}:{prediction.contract_id}:"
            f"{cutoff.isoformat()}"
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return OutcomeContract(
            outcome_contract_id=f"outcome_contract_{digest}",
            version=OUTCOME_CONTRACT_VERSION,
            prediction_contract_id=prediction.contract_id,
            prediction_version=prediction.version,
            symbol=prediction.symbol,
            secid=prediction.secid,
            t0=prediction.t0,
            evaluated_through=cutoff.isoformat(),
            p0=prediction.p0,
            up_barrier=prediction.up_barrier,
            down_barrier=prediction.down_barrier,
            available_future_sessions=len(future_sessions),
            horizons=horizons,
            status=status,
        )

    def _evaluate_horizon(
        self,
        prediction: PredictionContract,
        instrument: Instrument,
        t0: datetime,
        future_sessions: tuple[Candle, ...],
        m15_candles: tuple[Candle, ...],
        horizon: int,
    ) -> HorizonOutcomeContract:
        if len(future_sessions) < horizon:
            return HorizonOutcomeContract(
                horizon_sessions=horizon,
                status="PENDING",
                outcome=None,
                target_session_end=None,
                observation_end=None,
                first_hit_at=None,
                first_hit_window_start=None,
                first_hit_window_end=None,
                hit_time_resolution=None,
                hit_session_index=None,
                time_to_hit_minutes=None,
                max_up_excursion_pct=None,
                max_down_excursion_pct=None,
                mfe_pct=None,
                mae_pct=None,
            )

        sessions = future_sessions[:horizon]
        expected_dates = {
            _parse_time(candle.begin).date()
            for candle in sessions
        }
        bars = tuple(
            candle
            for candle in m15_candles
            if _parse_time(candle.begin).date() in expected_dates
            and _parse_time(candle.begin) > t0
        )
        present_dates = {
            _parse_time(candle.begin).date()
            for candle in bars
        }
        target = sessions[-1]
        if expected_dates - present_dates:
            return HorizonOutcomeContract(
                horizon_sessions=horizon,
                status="DATA_INCOMPLETE",
                outcome=None,
                target_session_end=target.end,
                observation_end=bars[-1].end if bars else None,
                first_hit_at=None,
                first_hit_window_start=None,
                first_hit_window_end=None,
                hit_time_resolution=None,
                hit_session_index=None,
                time_to_hit_minutes=None,
                max_up_excursion_pct=_max_up_excursion(bars, prediction.p0),
                max_down_excursion_pct=_max_down_excursion(bars, prediction.p0),
                mfe_pct=_max_up_excursion(bars, prediction.p0),
                mae_pct=_max_down_excursion(bars, prediction.p0),
                ambiguity_reason=(
                    "M15 coverage is incomplete for one or more expected sessions"
                ),
            )

        touch = _scan_candles(
            bars,
            prediction.up_barrier,
            prediction.down_barrier,
            resolution="M15",
        )
        if touch.outcome == "AMBIGUOUS":
            touch = self._refine_ambiguous(
                instrument,
                prediction,
                touch,
            )

        session_index = None
        if touch.window_start is not None:
            hit_date = _parse_time(touch.window_start).date()
            ordered_dates = [
                _parse_time(candle.begin).date()
                for candle in sessions
            ]
            try:
                session_index = ordered_dates.index(hit_date) + 1
            except ValueError:
                session_index = None

        first_hit_time = (
            _parse_time(touch.first_hit_at)
            if touch.first_hit_at is not None
            else None
        )
        time_to_hit = (
            round((first_hit_time - t0).total_seconds() / 60.0, 3)
            if first_hit_time is not None
            else None
        )
        up_excursion = _max_up_excursion(bars, prediction.p0)
        down_excursion = _max_down_excursion(bars, prediction.p0)
        return HorizonOutcomeContract(
            horizon_sessions=horizon,
            status="OBSERVED",
            outcome=touch.outcome,  # type: ignore[arg-type]
            target_session_end=target.end,
            observation_end=bars[-1].end if bars else target.end,
            first_hit_at=touch.first_hit_at,
            first_hit_window_start=touch.window_start,
            first_hit_window_end=touch.window_end,
            hit_time_resolution=touch.resolution,
            hit_session_index=session_index,
            time_to_hit_minutes=time_to_hit,
            max_up_excursion_pct=up_excursion,
            max_down_excursion_pct=down_excursion,
            mfe_pct=up_excursion,
            mae_pct=down_excursion,
            ambiguity_reason=touch.ambiguity_reason,
        )

    def _refine_ambiguous(
        self,
        instrument: Instrument,
        prediction: PredictionContract,
        coarse: _Touch,
    ) -> _Touch:
        if coarse.window_start is None or coarse.window_end is None:
            return coarse
        start = _parse_time(coarse.window_start)
        end = _parse_time(coarse.window_end)
        try:
            minute_series = self.market_data.candles_for_instrument(
                instrument,
                timeframe="M1",
                from_date=start.date().isoformat(),
                till_date=(end.date() + timedelta(days=1)).isoformat(),
                completed_only=True,
            )
            minute_bars = tuple(
                candle
                for candle in minute_series.candles
                if _parse_time(candle.begin) >= start
                and _parse_time(candle.begin) <= end
            )
        except Exception:
            minute_bars = ()

        if minute_bars:
            minute_touch = _scan_candles(
                minute_bars,
                prediction.up_barrier,
                prediction.down_barrier,
                resolution="M1",
            )
            if minute_touch.outcome != "AMBIGUOUS":
                return minute_touch
            trade_touch = self._refine_with_public_trades(
                instrument,
                prediction,
                minute_touch,
            )
            if trade_touch is not None:
                return trade_touch
            return _Touch(
                outcome="AMBIGUOUS",
                window_start=minute_touch.window_start,
                window_end=minute_touch.window_end,
                first_hit_at=None,
                resolution="M1",
                ambiguity_reason=(
                    "both barriers were touched inside one M1 candle and "
                    "trade-level ordering was unavailable"
                ),
            )

        return _Touch(
            outcome="AMBIGUOUS",
            window_start=coarse.window_start,
            window_end=coarse.window_end,
            first_hit_at=None,
            resolution="M15",
            ambiguity_reason=(
                "both barriers were touched inside one M15 candle and "
                "M1 refinement was unavailable"
            ),
        )

    def _refine_with_public_trades(
        self,
        instrument: Instrument,
        prediction: PredictionContract,
        minute_touch: _Touch,
    ) -> _Touch | None:
        if (
            self.analytics is None
            or minute_touch.window_start is None
            or minute_touch.window_end is None
        ):
            return None
        start = _parse_time(minute_touch.window_start)
        end = _parse_time(minute_touch.window_end)
        try:
            rows = self.analytics.fetch_public_recent_trades(
                instrument,
                max_pages=50,
            )
        except Exception:
            return None

        trades: list[tuple[datetime, int, float]] = []
        for row in rows:
            timestamp = _trade_time(row)
            price = _float_or_none(row.get("PRICE") or row.get("price"))
            if timestamp is None or price is None:
                continue
            if timestamp < start or timestamp > end:
                continue
            sequence = int(
                row.get("RECNO")
                or row.get("recno")
                or row.get("TRADENO")
                or row.get("tradeno")
                or 0
            )
            trades.append((timestamp, sequence, price))
        trades.sort(key=lambda item: (item[0], item[1]))

        for timestamp, _, price in trades:
            if price >= prediction.up_barrier:
                return _Touch(
                    outcome="UP_FIRST",
                    window_start=timestamp.isoformat(),
                    window_end=timestamp.isoformat(),
                    first_hit_at=timestamp.isoformat(),
                    resolution="TRADE",
                )
            if price <= prediction.down_barrier:
                return _Touch(
                    outcome="DOWN_FIRST",
                    window_start=timestamp.isoformat(),
                    window_end=timestamp.isoformat(),
                    first_hit_at=timestamp.isoformat(),
                    resolution="TRADE",
                )
        return None

    def _exact_instrument(
        self,
        prediction: PredictionContract,
        *,
        t0: datetime,
    ) -> Instrument:
        resolver = self.market_data.direct_resolver
        if resolver is not None:
            instrument = resolver.resolve(prediction.secid)
            if instrument is not None:
                if instrument.secid != prediction.secid:
                    raise ValueError(
                        "current resolver returned a different SECID than "
                        "the Prediction Contract"
                    )
                return instrument

        historical = self.market_data.historical_future_resolver
        if historical is not None:
            instrument = historical.resolve(prediction.symbol, t0.date())
            if instrument.secid != prediction.secid:
                raise ValueError(
                    "historical contract at Prediction Contract T0 does not "
                    "match its immutable SECID"
                )
            return instrument

        raise ValueError(
            f"Prediction Contract SECID is no longer resolvable: "
            f"{prediction.secid}"
        )


def _scan_candles(
    candles: Iterable[Candle],
    up_barrier: float,
    down_barrier: float,
    *,
    resolution: str,
) -> _Touch:
    for candle in sorted(candles, key=lambda item: item.begin):
        hit_up = candle.high is not None and float(candle.high) >= up_barrier
        hit_down = candle.low is not None and float(candle.low) <= down_barrier
        if hit_up and hit_down:
            return _Touch(
                outcome="AMBIGUOUS",
                window_start=candle.begin,
                window_end=candle.end,
                first_hit_at=None,
                resolution=resolution,
                ambiguity_reason=(
                    f"both barriers were touched inside one {resolution} candle"
                ),
            )
        if hit_up:
            return _Touch(
                outcome="UP_FIRST",
                window_start=candle.begin,
                window_end=candle.end,
                first_hit_at=candle.end,
                resolution=resolution,
            )
        if hit_down:
            return _Touch(
                outcome="DOWN_FIRST",
                window_start=candle.begin,
                window_end=candle.end,
                first_hit_at=candle.end,
                resolution=resolution,
            )
    return _Touch(
        outcome="NEITHER",
        window_start=None,
        window_end=None,
        first_hit_at=None,
        resolution=None,
    )


def _max_up_excursion(candles: Iterable[Candle], p0: float) -> float | None:
    highs = [float(candle.high) for candle in candles if candle.high is not None]
    if not highs or p0 == 0:
        return None
    return round(max(0.0, (max(highs) / p0 - 1.0) * 100.0), 6)


def _max_down_excursion(candles: Iterable[Candle], p0: float) -> float | None:
    lows = [float(candle.low) for candle in candles if candle.low is not None]
    if not lows or p0 == 0:
        return None
    return round(max(0.0, (1.0 - min(lows) / p0) * 100.0), 6)


def _trade_time(row: dict[str, object]) -> datetime | None:
    trade_date = str(
        row.get("TRADEDATE")
        or row.get("tradedate")
        or ""
    ).strip()
    trade_time = str(
        row.get("TRADETIME")
        or row.get("tradetime")
        or ""
    ).strip()
    if not trade_date or not trade_time:
        return None
    try:
        parsed = datetime.fromisoformat(f"{trade_date}T{trade_time}")
    except ValueError:
        return None
    return parsed.replace(tzinfo=MOEX_TIMEZONE)


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MOEX_TIMEZONE)
    return parsed.astimezone(MOEX_TIMEZONE)
