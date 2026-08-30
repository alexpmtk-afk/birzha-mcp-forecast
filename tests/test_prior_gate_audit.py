from __future__ import annotations

from datetime import date

from birzha.application.flow import MarketFlowService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import MoexAnalyticsClient, MoexAnalyticsError
from birzha.providers.moex_iss import MoexIssClient


HISTORICAL_SI = Instrument(
    symbol="Si",
    secid="SiM6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)


class FakeMarketData:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date | None]] = []

    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        self.calls.append((symbol, as_of))
        return HISTORICAL_SI


class EmptyAnalytics:
    def fetch_tradestats(self, *args, **kwargs):
        return []

    def fetch_futoi(self, *args, **kwargs):
        return []


def test_market_flow_resolves_historical_future_at_requested_till_date() -> None:
    market_data = FakeMarketData()
    service = MarketFlowService(market_data=market_data, analytics=EmptyAnalytics())  # type: ignore[arg-type]

    result = service.build("Si", till_date="2026-04-01", lookback_days=2)

    assert market_data.calls == [("Si", date(2026, 4, 1))]
    assert result.secid == "SiM6"


def test_market_flow_cutoff_has_priority_for_historical_resolution() -> None:
    market_data = FakeMarketData()
    service = MarketFlowService(market_data=market_data, analytics=EmptyAnalytics())  # type: ignore[arg-type]

    service.build(
        "Si",
        till_date="2026-04-05",
        cutoff_at="2026-04-01T18:00:00+03:00",
        lookback_days=2,
    )

    assert market_data.calls == [("Si", date(2026, 4, 1))]


def test_public_market_and_unauthenticated_analytics_share_one_pacing_gate() -> None:
    control = ProcessUpstreamControlPlane()
    market = MoexIssClient(control_plane=control)
    analytics = MoexAnalyticsClient(control_plane=control, bearer_token="")

    market_gate = market._governor._executor._gate  # noqa: SLF001 - regression of composition contract
    algopack_gate = analytics._algopack_governor._executor._gate  # noqa: SLF001
    public_gate = analytics._public_governor._executor._gate  # noqa: SLF001

    assert market_gate is algopack_gate
    assert market_gate is public_gate


def test_authenticated_algopack_keeps_stricter_separate_gate() -> None:
    control = ProcessUpstreamControlPlane()
    market = MoexIssClient(control_plane=control)
    analytics = MoexAnalyticsClient(control_plane=control, bearer_token="token")

    market_gate = market._governor._executor._gate  # noqa: SLF001
    auth_gate = analytics._algopack_governor._executor._gate  # noqa: SLF001

    assert market_gate is not auth_gate
