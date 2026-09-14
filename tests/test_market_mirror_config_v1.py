from __future__ import annotations

import pytest

from birzha.config import Settings


MIRROR_VARS = (
    "BIRZHA_MARKET_MIRROR_REQUIRED",
    "BIRZHA_MARKET_MIRROR_BRIDGE_URL",
    "BIRZHA_MARKET_MIRROR_BRIDGE_SECRET",
    "BIRZHA_MARKET_MIRROR_ROOT_FOLDER_ID",
)


def clear(monkeypatch) -> None:
    for name in MIRROR_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BIRZHA_STATE_BACKEND", raising=False)
    monkeypatch.delenv("YDB_CONNECTION_STRING", raising=False)


def test_market_mirror_optional_and_empty_by_default(monkeypatch):
    clear(monkeypatch)
    settings = Settings.from_env()
    assert settings.market_mirror_required is False
    assert settings.market_mirror_bridge_url is None
    assert settings.market_mirror_bridge_secret is None
    assert settings.market_mirror_root_folder_id is None


def test_market_mirror_rejects_partial_config(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("BIRZHA_MARKET_MIRROR_BRIDGE_URL", "https://script.google.com/macros/s/birzha/exec")
    with pytest.raises(ValueError, match="requires URL, secret, and root"):
        Settings.from_env()


def test_market_mirror_required_rejects_missing_config(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("BIRZHA_MARKET_MIRROR_REQUIRED", "true")
    with pytest.raises(ValueError, match="requires complete Bridge v1"):
        Settings.from_env()


def test_market_mirror_required_accepts_complete_isolated_config(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("BIRZHA_MARKET_MIRROR_REQUIRED", "true")
    monkeypatch.setenv("BIRZHA_MARKET_MIRROR_BRIDGE_URL", "https://script.google.com/macros/s/birzha/exec")
    monkeypatch.setenv("BIRZHA_MARKET_MIRROR_BRIDGE_SECRET", "project-secret")
    monkeypatch.setenv("BIRZHA_MARKET_MIRROR_ROOT_FOLDER_ID", "birzha-root")
    settings = Settings.from_env()
    assert settings.market_mirror_required is True
    assert settings.market_mirror_bridge_url.endswith("/birzha/exec")
    assert settings.market_mirror_bridge_secret == "project-secret"
    assert settings.market_mirror_root_folder_id == "birzha-root"
