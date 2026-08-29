from birzha.mcp.server import system_version


def test_system_version_contract():
    assert system_version() == {
        "service": "BIRZHA MCP FORECAST",
        "version": "0.1.0",
        "architecture": "BIRZHA_MCP_FORECAST_V0_2",
    }
