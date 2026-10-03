from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from birzha.application.outcome_contract import OutcomeContractService
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.domain.prediction import PredictionContract


INSTRUMENT = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
)


class Resolver:
    def resolve(self, secid: str):
        return INSTRUMENT if secid == "SBER" else None


@dataclass
class PredictionService:
    contract: PredictionContract

    def build(self, symbol: str, *, as_of_date: str | None = None):
        assert symbol == "SBER"
        return self.contract


class Market:
    direct_resolver = Resolver()
    historical_future_resolver = None

    def __init__(self, *, m15: tuple[Candle, ...], m1: tuple[Candle, ...] = ()):
        self.m15 = m15
        self.m1 = m1

    def candles_for_instrument(self, instrument, *, timeframe, **kwargs):
        if timeframe == "D1":
            candles = tuple(
                _candle(
                    f"2026-09-{day:02d}T10:00:00+03:00",
                    f"2026-09-{day:02d}T23:49:59+03:00",
                    100,
                    100,
                    101,
                    99,
                )
                for day in range(2, 7)
            )
        elif timeframe == "M15":
            candles = self.m15
        elif timeframe == "M1":
            candles = self.m1
        else:
            raise AssertionError(timeframe)
        return CandleSeries(
            instrument=instrument,
            timeframe=timeframe,
            candles=candles,
            source="TEST",
        )


class Analytics:
    def __init__(self, rows):
        self.rows = rows

    def fetch_public_recent_trades(self, instrument, *, max_pages=50):
        return self.rows


def _prediction() -> PredictionContract:
    return PredictionContract(
        contract_id="pred_test",
        version="PREDICTION_CONTRACT_V1_ATR14_K1",
        symbol="SBER",
        secid="SBER",
        t0="2026-09-01T18:45:00+03:00",
        p0=100.0,
        price_coordinate="LAST_COMPLETED_M15_CLOSE",
        significant_move_definition="P0 +/- k * causal D1 ATR(14)",
        volatility_measure="D1_WILDER_ATR14_PRICE",
        causal_volatility_price=2.0,
        barrier_k=1.0,
        up_barrier=102.0,
        down_barrier=98.0,
        horizons_sessions=(5, 10, 20),
        up_hit_rule="price >= up_barrier",
        down_hit_rule="price <= down_barrier",
        ambiguous_path_policy="M15 -> M1 -> TRADES -> AMBIGUOUS_PATH",
        rollover_policy="OUT_OF_SCOPE_ROLLOVER_V0",
    )


def _candle(begin, end, open_, close, high, low):
    return Candle(
        open_,
        close,
        high,
        low,
        None,
        1,
        begin,
        end,
        True,
    )


def _five_session_m15(*, ambiguous=False):
    rows = []
    for day in range(2, 7):
        high = 101.0
        low = 99.0
        if day == 3:
            high = 103.0
            low = 97.0 if ambiguous else 99.0
        rows.append(
            _candle(
                f"2026-09-{day:02d}T10:00:00+03:00",
                f"2026-09-{day:02d}T10:14:59+03:00",
                100.0,
                100.5,
                high,
                low,
            )
        )
    return tuple(rows)


def test_outcome_contract_classifies_up_first_and_reports_excursions():
    service = OutcomeContractService(
        Market(m15=_five_session_m15()),
        PredictionService(_prediction()),  # type: ignore[arg-type]
    )

    result = service.evaluate(
        "SBER",
        as_of_date="2026-09-01",
        evaluation_date="2026-09-06",
    )
    by_horizon = {item.horizon_sessions: item for item in result.horizons}
    observed = by_horizon[5]

    assert result.status == "PARTIAL"
    assert observed.status == "OBSERVED"
    assert observed.outcome == "UP_FIRST"
    assert observed.hit_session_index == 2
    assert observed.hit_time_resolution == "M15"
    assert observed.mfe_pct == 3.0
    assert observed.mae_pct == 1.0
    assert by_horizon[10].status == "PENDING"
    assert by_horizon[20].status == "PENDING"


def test_ambiguous_m15_is_resolved_by_minute_sequence():
    ambiguous = _five_session_m15(ambiguous=True)
    m1 = (
        _candle(
            "2026-09-03T10:00:00+03:00",
            "2026-09-03T10:00:59+03:00",
            100,
            99,
            101,
            97,
        ),
        _candle(
            "2026-09-03T10:01:00+03:00",
            "2026-09-03T10:01:59+03:00",
            99,
            102,
            103,
            99,
        ),
    )
    service = OutcomeContractService(
        Market(m15=ambiguous, m1=m1),
        PredictionService(_prediction()),  # type: ignore[arg-type]
    )

    result = service.evaluate(
        "SBER",
        evaluation_date="2026-09-06",
    )
    observed = result.horizons[0]

    assert observed.outcome == "DOWN_FIRST"
    assert observed.hit_time_resolution == "M1"
    assert observed.first_hit_window_start == "2026-09-03T10:00:00+03:00"


def test_ambiguous_minute_is_resolved_by_public_trade_order():
    ambiguous = _five_session_m15(ambiguous=True)
    m1 = (
        _candle(
            "2026-09-03T10:00:00+03:00",
            "2026-09-03T10:00:59+03:00",
            100,
            100,
            103,
            97,
        ),
    )
    rows = [
        {
            "TRADEDATE": "2026-09-03",
            "TRADETIME": "10:00:10",
            "TRADENO": 1,
            "PRICE": 102.5,
        },
        {
            "TRADEDATE": "2026-09-03",
            "TRADETIME": "10:00:20",
            "TRADENO": 2,
            "PRICE": 97.5,
        },
    ]
    service = OutcomeContractService(
        Market(m15=ambiguous, m1=m1),
        PredictionService(_prediction()),  # type: ignore[arg-type]
        Analytics(rows),  # type: ignore[arg-type]
    )

    result = service.evaluate(
        "SBER",
        evaluation_date="2026-09-06",
    )
    observed = result.horizons[0]

    assert observed.outcome == "UP_FIRST"
    assert observed.hit_time_resolution == "TRADE"
    assert observed.first_hit_at == "2026-09-03T10:00:10+03:00"


def test_unresolved_same_minute_path_is_explicitly_ambiguous():
    ambiguous = _five_session_m15(ambiguous=True)
    m1 = (
        _candle(
            "2026-09-03T10:00:00+03:00",
            "2026-09-03T10:00:59+03:00",
            100,
            100,
            103,
            97,
        ),
    )
    service = OutcomeContractService(
        Market(m15=ambiguous, m1=m1),
        PredictionService(_prediction()),  # type: ignore[arg-type]
        Analytics([]),  # type: ignore[arg-type]
    )

    result = service.evaluate(
        "SBER",
        evaluation_date="2026-09-06",
    )
    observed = result.horizons[0]

    assert observed.outcome == "AMBIGUOUS"
    assert observed.hit_time_resolution == "M1"
    assert observed.first_hit_at is None
    assert "trade-level ordering was unavailable" in str(
        observed.ambiguity_reason
    )
