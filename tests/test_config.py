from birzha.config import LOCAL_ALLOWED_HOSTS, Settings


def test_default_settings(monkeypatch):
    for name in ("HOST", "PORT", "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)

    assert Settings.from_env() == Settings(
        host="0.0.0.0",
        port=8080,
        mcp_allowed_hosts=LOCAL_ALLOWED_HOSTS,
        mcp_allowed_origins=(),
    )


def test_runtime_port(monkeypatch):
    monkeypatch.setenv("PORT", "9090")
    assert Settings.from_env().port == 9090


def test_remote_security_allowlists(monkeypatch):
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
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "")
    assert Settings.from_env().mcp_allowed_hosts == ()
