from __future__ import annotations

from dataclasses import dataclass

from birzha.application.market_data import MarketDataService
from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import MoexAnalyticsError, _algopack_market_code
from birzha.providers.moex_resolver import MoexDirectInstrumentResolver


SBER = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
    name="Sberbank",
)
SI = Instrument(
    symbol="Si",
    secid="SiU6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)


@dataclass
class FakeDirectResolver:
    def resolve(self, symbol: str) -> Instrument | None:
        return SBER if symbol.upper() == "SBER" else None


@dataclass
class FakeProvider:
    futures_calls: int = 0

    def resolve_active_future(self, symbol: str) -> Instrument:
        self.futures_calls += 1
        assert symbol == "Si"
        return SI


def test_exact_equity_uses_direct_moex_route_and_futures_root_falls_back() -> None:
    provider = FakeProvider()
    service = MarketDataService(
        provider=provider,  # type: ignore[arg-type]
        direct_resolver=FakeDirectResolver(),  # type: ignore[arg-type]
    )

    assert service.resolve("SBER") == SBER
    assert provider.futures_calls == 0
    assert service.resolve("Si") == SI
    assert provider.futures_calls == 1


def test_algopack_route_is_asset_class_based_not_symbol_based() -> None:
    assert _algopack_market_code(SBER) == "eq"
    assert _algopack_market_code(SI) == "fo"
    unsupported = Instrument("IMOEX", "IMOEX", "SNDX", "stock", "index", "index")
    try:
        _algopack_market_code(unsupported)
    except MoexAnalyticsError:
        pass
    else:
        raise AssertionError("unsupported asset class must fail closed")


class FakeResponse:
    def json(self):
        return {
            "boards": {
                "columns": [
                    "secid", "boardid", "title", "market", "engine",
                    "is_primary", "listed_from", "listed_till", "has_candles",
                ],
                "data": [
                    ["SBER", "TQBR", "Sberbank", "shares", "stock", 1, "2007-07-20", None, 1],
                ],
            }
        }


class FakeIssClient:
    def _request(self, path, params):
        assert path == "/securities/SBER.json"
        return FakeResponse()

    @staticmethod
    def _table(payload, name):
        table = payload[name]
        return [dict(zip(table["columns"], row, strict=False)) for row in table["data"]]


def test_direct_resolver_maps_real_moex_board_semantics_to_equity() -> None:
    resolver = MoexDirectInstrumentResolver(FakeIssClient())  # type: ignore[arg-type]
    instrument = resolver.resolve("sber")

    assert instrument is not None
    assert instrument.secid == "SBER"
    assert instrument.board == "TQBR"
    assert instrument.engine == "stock"
    assert instrument.market == "shares"
    assert instrument.asset_class == "equity"
