from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from birzha.application.outcome import OutcomeService
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal


INSTRUMENT = Instrument(
    symbol="SiU6", secid="SiU6", board="RFUD", engine="futures", market="forts",
    asset_class="future", root_symbol="Si", last_trade_date="2026-09-17",
)


class Resolver:
    def resolve(self, secid: str):
        return INSTRUMENT if secid == "SiU6" else None


class Market:
    direct_resolver = Resolver()

    def candles_for_instrument(self, instrument, **kwargs):
        candles = [
            Candle(100, 100, 101, 99, None, 1, "2026-08-28T10:00:00+03:00", "2026-08-28T23:49:59+03:00", True),
        ]
        for index in range(1, 21):
            begin = f"2026-09-{index:02d}T10:00:00+03:00"
            end = f"2026-09-{index:02d}T23:49:59+03:00"
            close = 100.0 + index
            candles.append(Candle(close - 0.5, close, close + 1, close - 1, None, 1, begin, end, True))
        return CandleSeries(instrument=instrument, timeframe="D1", candles=tuple(candles))


class ForbiddenResolver:
    def resolve(self, *args, **kwargs):
        raise AssertionError("stored outcome path must not call live resolver")


class StoredOutcomeMarket:
    direct_resolver = ForbiddenResolver()
    historical_future_resolver = ForbiddenResolver()

    def stored_instrument(self, secid: str):
        return INSTRUMENT if secid == "SiU6" else None


def record() -> ForecastRecord:
    return ForecastRecord(
        forecast_id="fcst_outcome_test", symbol="Si", secid="SiU6",
        created_at_t0="2026-08-28T13:00:00+03:00",
        engine_version="BIRZHA_FORECAST_BASELINE_V0_2_FLOW", direction="UP",
        signal_strength=0.5, control="BUYERS", route="TREND",
        horizons=(
            HorizonForecast(5, "UP", 0.5, 2.0, 1.0),
            HorizonForecast(10, "UP", 0.5, 3.0, 1.5),
            HorizonForecast(20, "UP", 0.5, 4.0, 2.0),
        ), reasons=(), warnings=(), validation_status="UNVALIDATED_BASELINE",
        reference_price=100.0,
    )


def test_outcome_engine_observes_real_session_horizons_idempotently():
    forecasts = DuckDBForecastJournal(":memory:")
    outcomes = DuckDBOutcomeJournal(":memory:")
    forecasts.append(record())
    service = OutcomeService(Market(), forecasts, outcomes)

    first = service.evaluate("fcst_outcome_test", evaluation_date="2026-09-30")
    second = service.evaluate("fcst_outcome_test", evaluation_date="2026-09-30")

    assert first.status == "COMPLETE"
    assert [item.horizon_sessions for item in first.outcomes] == [5, 10, 20]
    assert [item.actual_return_pct for item in first.outcomes] == [5.0, 10.0, 20.0]
    assert all(item.direction_hit is True for item in first.outcomes)
    assert second.to_dict() == first.to_dict()
    assert len(outcomes.list_for_forecast("fcst_outcome_test")) == 3


def test_neutral_direction_is_not_forced_into_binary_hit_metric():
    forecasts = DuckDBForecastJournal(":memory:")
    outcomes = DuckDBOutcomeJournal(":memory:")
    forecasts.append(replace(record(), forecast_id="fcst_neutral", direction="NEUTRAL", control="BALANCE"))
    result = OutcomeService(Market(), forecasts, outcomes).evaluate(
        "fcst_neutral", evaluation_date="2026-09-30"
    )
    assert all(item.direction_hit is None for item in result.outcomes)


def test_history_backed_outcome_recovers_exact_contract_without_live_resolution():
    service = OutcomeService(  # type: ignore[arg-type]
        StoredOutcomeMarket(),
        object(),
        object(),
    )

    instrument = service._exact_instrument(
        record(),
        t0=datetime.fromisoformat("2026-08-28T13:00:00+03:00"),
    )

    assert instrument is INSTRUMENT
    assert instrument.secid == "SiU6"
