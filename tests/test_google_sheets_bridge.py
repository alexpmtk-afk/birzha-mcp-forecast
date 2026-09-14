from __future__ import annotations

import pytest

from birzha.storage.google_sheets_bridge import (
    GoogleSheetsBridge,
    GoogleSheetsBridgeConfig,
    GoogleSheetsBridgeError,
)


def _bridge() -> GoogleSheetsBridge:
    return GoogleSheetsBridge(
        GoogleSheetsBridgeConfig(
            bridge_url="https://script.google.com/macros/s/test/exec",
            bridge_secret="secret",
            root_folder_id="root-id",
        )
    )


def test_validated_sheets_rejects_unknown_sheet() -> None:
    with pytest.raises(ValueError, match="unsupported mirror sheet"):
        GoogleSheetsBridge._validated_sheets({"BAD": [["x"], [1]]})


def test_validated_sheets_rejects_non_rectangular_rows() -> None:
    with pytest.raises(ValueError, match="not rectangular"):
        GoogleSheetsBridge._validated_sheets({"D1": [["a", "b"], [1]]})


def test_generic_action_is_rejected_before_network() -> None:
    bridge = _bridge()
    with pytest.raises(ValueError, match="birzha_ namespace"):
        bridge._post("health")


def test_health_uses_namespaced_action_and_fails_on_root_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _bridge()
    captured = {}

    def fake_post(action: str, **payload):
        captured["action"] = action
        return {"ok": True, "root_id": "wrong-root"}

    monkeypatch.setattr(bridge, "_post", fake_post)
    with pytest.raises(GoogleSheetsBridgeError, match="root mismatch"):
        bridge.health()
    assert captured["action"] == "birzha_health"


def test_ensure_archive_requires_complete_target(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _bridge()
    monkeypatch.setattr(
        bridge,
        "_post",
        lambda action, **payload: {"ok": True, "spreadsheet_id": "sheet-only"},
    )
    with pytest.raises(GoogleSheetsBridgeError, match="incomplete archive target"):
        bridge.ensure_archive(symbol="BR")


def test_ensure_archive_resolves_by_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _bridge()
    captured = {}

    def fake_post(action: str, **payload):
        captured["action"] = action
        captured.update(payload)
        return {
            "ok": True,
            "spreadsheet_id": "sheet-id",
            "folder_id": "br-folder",
            "spreadsheet_name": "BIRZHA — BR — Market Data Mirror",
        }

    monkeypatch.setattr(bridge, "_post", fake_post)
    result = bridge.ensure_archive(symbol="BR")
    assert result["spreadsheet_id"] == "sheet-id"
    assert captured == {"action": "birzha_ensure_archive", "symbol": "BR"}


def test_replace_snapshot_requires_bridge_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _bridge()
    monkeypatch.setattr(
        bridge,
        "_post",
        lambda action, **payload: {"ok": True, "parity": False},
    )
    with pytest.raises(GoogleSheetsBridgeError, match="read-back parity failed"):
        bridge.replace_snapshot(spreadsheet_id="sheet", sheets={"D1": [["a"], [1]]})


def test_replace_snapshot_accepts_verified_bridge_response(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _bridge()
    captured = {}

    def fake_post(action: str, **payload):
        captured["action"] = action
        captured.update(payload)
        return {"ok": True, "parity": True, "sheets": {"D1": {"data_rows": 1}}}

    monkeypatch.setattr(bridge, "_post", fake_post)
    result = bridge.replace_snapshot(
        spreadsheet_id="sheet-id",
        sheets={"D1": [["a", "b"], [1, 2]]},
    )
    assert result["parity"] is True
    assert captured["action"] == "birzha_replace_snapshot"
    assert captured["spreadsheet_id"] == "sheet-id"


def test_summary_uses_namespaced_action(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _bridge()
    captured = {}

    def fake_post(action: str, **payload):
        captured["action"] = action
        captured.update(payload)
        return {"ok": True, "sheets": {}}

    monkeypatch.setattr(bridge, "_post", fake_post)
    bridge.summary(spreadsheet_id="sheet-id")
    assert captured == {"action": "birzha_summary", "spreadsheet_id": "sheet-id"}
