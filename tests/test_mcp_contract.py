import birzha.mcp.server as server
from birzha.mcp.server import market_state, system_version


def test_system_version_contract():
    assert system_version() == {
        "service": "BIRZHA MCP FORECAST",
        "version": "0.1.0",
        "architecture": "BIRZHA_MCP_FORECAST_V0_2",
    }


def test_market_state_mcp_tool_delegates_to_the_vector_service(monkeypatch):
    class ServiceStub:
        def build(self, symbol, *, as_of_date=None):
            return {"symbol": symbol, "as_of": as_of_date, "schema": "MARKET_STATE_VECTOR_V0"}

    monkeypatch.setattr(server, "_market_state", ServiceStub())

    assert market_state("Si", as_of_date="2026-10-01") == {
        "symbol": "Si",
        "as_of": "2026-10-01",
        "schema": "MARKET_STATE_VECTOR_V0",
    }
