from datetime import date

from birzha.application.validation import WalkForwardValidator


class History:
    def __init__(self): self.calls=[]
    def sync(self,symbol,*,timeframe,from_date,till_date):
        self.calls.append((symbol,timeframe,from_date,till_date))


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
