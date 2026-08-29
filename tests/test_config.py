from birzha.config import Settings


def test_default_settings(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert Settings.from_env() == Settings(host="0.0.0.0", port=8080)


def test_cloud_run_port(monkeypatch):
    monkeypatch.setenv("PORT", "9090")
    assert Settings.from_env().port == 9090
