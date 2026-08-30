from datetime import date

from birzha.application.validation import WalkForwardValidator
from birzha.domain.market import Instrument
from birzha.providers.moex_iss import MoexIssError


INSTRUMENT = Instrument(
    symbol="Si",
    secid="SiM6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)


class HistoricalResolver:
    def resolve(self, symbol: str, as_of: date) -> Instrument:
        assert symbol == "Si"
        if as_of <= date(2026, 5, 29):
            return INSTRUMENT
        raise MoexIssError("no session")


class MarketData:
    direct_resolver = None
    historical_future_resolver = HistoricalResolver()


class Calendar:
    def dates(self, **kwargs):
        start = kwargs["from_date"]
        if start <= date(2026, 5, 28):
            return (date(2026, 5, 28), date(2026, 5, 29))
        return ()


def test_trailing_weekend_after_last_futures_session_is_normal_end_of_calendar() -> None:
    validator = WalkForwardValidator(
        market_data=MarketData(),  # type: ignore[arg-type]
        forecasts=None,  # type: ignore[arg-type]
        calendar=Calendar(),  # type: ignore[arg-type]
    )
    sessions = validator._session_dates(
        "Si",
        start=date(2026, 5, 28),
        end=date(2026, 5, 31),
    )
    assert sessions == (date(2026, 5, 28), date(2026, 5, 29))
