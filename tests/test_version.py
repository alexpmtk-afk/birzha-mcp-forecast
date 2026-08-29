from birzha.version import ARCHITECTURE_VERSION, SERVICE_NAME, VERSION


def test_service_metadata():
    assert SERVICE_NAME == "BIRZHA MCP FORECAST"
    assert VERSION == "0.1.0"
    assert ARCHITECTURE_VERSION == "BIRZHA_MCP_FORECAST_V0_2"
