import pytest

from birzha.config import LOCAL_ALLOWED_HOSTS, Settings


def _clear(monkeypatch):
    for name in (
        "HOST", "PORT", "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS",
        "BIRZHA_STATE_BACKEND", "YDB_CONNECTION_STRING", "BIRZHA_FORECAST_JOURNAL_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_settings(monkeypatch):
    _clear(monkeypatch)
    settings = Settings.from_env()
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.mcp_allowed_hosts == LOCAL_ALLOWED_HOSTS
    assert settings.mcp_allowed_origins == ()
    assert settings.state_backend == "duckdb"
    assert settings.ydb_connection_string is None


def test_runtime_port(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PORT", "9090")
    assert Settings.from_env().port == 9090


def test_remote_security_allowlists(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(
        "MCP_ALLOWED_HOSTS",
        "example.apigw.yandexcloud.net,example.apigw.yandexcloud.net:*",
    )
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://chatgpt.com")
    settings = Settings.from_env()
    assert settings.mcp_allowed_hosts == (
        "example.apigw.yandexcloud.net",
        "example.apigw.yandexcloud.net:*",
    )
    assert settings.mcp_allowed_origins == ("https://chatgpt.com",)


def test_explicit_empty_remote_host_allowlist_is_fail_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "")
    assert Settings.from_env().mcp_allowed_hosts == ()


def test_ydb_requires_connection_string(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("BIRZHA_STATE_BACKEND", "ydb")
    with pytest.raises(ValueError, match="YDB_CONNECTION_STRING"):
        Settings.from_env()


def test_ydb_configuration(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("BIRZHA_STATE_BACKEND", "ydb")
    monkeypatch.setenv("YDB_CONNECTION_STRING", "grpcs://ydb.example.net:2135/?database=/ru/test/db")
    settings = Settings.from_env()
    assert settings.state_backend == "ydb"
    assert settings.ydb_connection_string.startswith("grpcs://")


def test_unknown_state_backend_fails_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("BIRZHA_STATE_BACKEND", "filesystem")
    with pytest.raises(ValueError, match="BIRZHA_STATE_BACKEND"):
        Settings.from_env()
