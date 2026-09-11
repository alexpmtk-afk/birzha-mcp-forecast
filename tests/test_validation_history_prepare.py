from datetime import date

from birzha.application.forecast import ForecastService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.validation import WalkForwardValidator


class History:
    def __init__(self): self.calls=[]
    def sync(self,symbol,*,timeframe,from_date,till_date):
        self.calls.append((symbol,timeframe,from_date,till_date))
    def session_dates(self,symbol,*,from_date,till_date):
        return (date(2026,7,1), date(2026,7,2))


class FlowHistory:
    def __init__(self): self.calls=[]
    def sync(self,symbol,*,from_date,till_date):
        self.calls.append((symbol,from_date,till_date))


def test_prepare_history_primes_all_price_timeframes_and_flow():
    history=History(); flow=FlowHistory()
    validator=WalkForwardValidator(market_data=None,forecasts=None,calendar=None,history=history,historical_flow=flow)
    validator._prepare_history("SBER",start=date(2026,7,1),end=date(2026,9,1))
    assert [item[1] for item in history.calls] == ["D1","H1","M15"]
    assert all(item[3] == "2026-09-01" for item in history.calls)
    assert flow.calls == [("SBER","2026-06-21","2026-09-01")]


def test_validation_uses_persisted_sessions_when_history_is_present():
    history=History()
    validator=WalkForwardValidator(market_data=None,forecasts=None,calendar=None,history=history)
    sessions=validator._validation_sessions("SBER",start=date(2026,7,1),end=date(2026,7,2))
    assert sessions == (date(2026,7,1), date(2026,7,2))


def test_frozen_validation_does_not_prepare_or_mutate_history(monkeypatch):
    history=History()
    forecasts=ForecastService(
        snapshots=MarketSnapshotService(market_data=None, flow=None)  # type: ignore[arg-type]
    )
    validator=WalkForwardValidator(
        market_data=None,  # type: ignore[arg-type]
        forecasts=forecasts,
        calendar=None,  # type: ignore[arg-type]
        history=history,  # type: ignore[arg-type]
        prepare_history_before_run=False,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("frozen validation must not prepare history")

    monkeypatch.setattr(validator, "_prepare_history", forbidden)
    report=validator.run(
        "SBER",
        start_date="2026-07-01",
        end_date="2026-07-02",
    )

    assert report.status == "NO_ELIGIBLE_POINTS"
    assert history.calls == []
