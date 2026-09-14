from __future__ import annotations

from typing import Any

import pytest

from birzha.storage.google_sheets_bridge import (
    BRIDGE_PROJECT_ID,
    BRIDGE_RELEASE,
    GoogleSheetsBridge,
    GoogleSheetsBridgeConfig,
    GoogleSheetsBridgeError,
)


URL = "https://script.google.com/macros/s/birzha-v1/exec"
ROOT = "birzha-root"


def bridge() -> GoogleSheetsBridge:
    return GoogleSheetsBridge(
        GoogleSheetsBridgeConfig(
            bridge_url=URL,
            bridge_secret="secret",
            root_folder_id=ROOT,
            chunk_rows=2,
        )
    )


def test_health_requires_stable_release_root_project_and_capabilities(monkeypatch):
    b = bridge()

    def fake_post(action: str, payload=None, *, idempotency_key=None):
        assert action == "health"
        assert payload is None
        assert idempotency_key is None
        return {
            "protocol_version": 1,
            "bridge_release": BRIDGE_RELEASE,
            "project_id": BRIDGE_PROJECT_ID,
            "root_id": ROOT,
            "capabilities": {
                "google_sheets_chunked": True,
                "google_sheets_inspect": True,
                "fixed_root_file_id_guard": True,
                "idempotent_mutations": True,
                "global_script_lock": False,
            },
        }

    monkeypatch.setattr(b, "_post", fake_post)
    assert b.health()["bridge_release"] == "1.0.0"

    def wrong_release(action: str, payload=None, *, idempotency_key=None):
        result = fake_post(action, payload, idempotency_key=idempotency_key)
        return {**result, "bridge_release": "1.0.0-alpha.2"}

    monkeypatch.setattr(b, "_post", wrong_release)
    with pytest.raises(GoogleSheetsBridgeError) as exc:
        b.health()
    assert exc.value.code == "BRIDGE_RELEASE_MISMATCH"


def test_ensure_archive_uses_birzha_mapping_and_known_br_spreadsheet(monkeypatch):
    b = bridge()
    seen: list[tuple[str, dict[str, Any], str | None]] = []

    def fake_post(action: str, payload=None, *, idempotency_key=None):
        payload = dict(payload or {})
        seen.append((action, payload, idempotency_key))
        if payload.get("spreadsheet_id"):
            return {
                "spreadsheet": {
                    "id": payload["spreadsheet_id"],
                    "name": "BIRZHA — BR — Market Data Mirror",
                },
                "created": False,
            }
        return {
            "spreadsheet": {
                "id": "si-sheet",
                "name": "MOEX_HISTDATA_Si_MCP_CANONICAL",
            },
            "created": True,
        }

    monkeypatch.setattr(b, "_post", fake_post)
    si = b.ensure_archive(symbol="si")
    br = b.ensure_archive(symbol="BR")

    assert si["spreadsheet_id"] == "si-sheet"
    assert seen[0][1]["path"] == "Si — Доллар-рубль"
    assert seen[0][1]["filename"] == "MOEX_HISTDATA_Si_MCP_CANONICAL"
    assert seen[0][2] and seen[0][2].startswith("birzha:sheet_ensure:")
    assert br["spreadsheet_id"] == "1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ"
    assert seen[1][1] == {"spreadsheet_id": br["spreadsheet_id"]}


def test_replace_snapshot_orders_stage_chunks_verify_commit_inspect(monkeypatch):
    b = bridge()
    calls: list[tuple[str, dict[str, Any], str | None]] = []
    stages: dict[str, str] = {}

    def fake_post(action: str, payload=None, *, idempotency_key=None):
        payload = dict(payload or {})
        calls.append((action, payload, idempotency_key))
        if action == "sheet_stage_begin":
            title = str(payload["sheet_title"])
            stage = f"__STAGE_{title}"
            stages[title] = stage
            return {"stage_sheet_title": stage, "replayed": False}
        if action == "sheet_write_chunk":
            return {"written": True}
        if action == "sheet_verify":
            stage = str(payload["stage_sheet_title"])
            title = stage.removeprefix("__STAGE_")
            rows = {"D1": 3, "SESSIONS": 2}[title]
            cols = {"D1": 2, "SESSIONS": 3}[title]
            return {"row_count": rows, "column_count": cols, "digest": ("a" if title == "D1" else "b") * 64}
        if action == "sheet_commit":
            return {"committed": True}
        if action == "sheet_inspect":
            title = str(payload["sheet_title"])
            rows = {"D1": 3, "SESSIONS": 2}[title]
            cols = {"D1": 2, "SESSIONS": 3}[title]
            return {
                "found": True,
                "row_count": rows,
                "column_count": cols,
                "digest": ("a" if title == "D1" else "b") * 64,
            }
        raise AssertionError(action)

    monkeypatch.setattr(b, "_post", fake_post)
    result = b.replace_snapshot(
        spreadsheet_id="sheet-1",
        sheets={
            "SESSIONS": [["symbol", "secid", "trade_date"], ["SI", "SiZ6", "2026-09-14"]],
            "D1": [["key", "date"], ["a", "2026-09-13"], ["b", "2026-09-14"]],
        },
    )

    assert result["parity"] is True
    assert result["sheets"]["D1"]["data_rows"] == 2
    actions = [action for action, _, _ in calls]
    # Canonical order is fixed independently of input dict order.
    assert actions[:5] == [
        "sheet_stage_begin",
        "sheet_write_chunk",
        "sheet_write_chunk",
        "sheet_verify",
        "sheet_commit",
    ]
    assert actions[5] == "sheet_inspect"
    assert actions[6] == "sheet_stage_begin"
    mutation_keys = [key for action, _, key in calls if action.startswith("sheet_") and action not in {"sheet_verify", "sheet_inspect"}]
    assert all(key and key.startswith("birzha:") for key in mutation_keys)


def test_replace_snapshot_aborts_uncommitted_stage_on_failure(monkeypatch):
    b = bridge()
    actions: list[str] = []

    def fake_post(action: str, payload=None, *, idempotency_key=None):
        actions.append(action)
        if action == "sheet_stage_begin":
            return {"stage_sheet_title": "__STAGE_D1"}
        if action == "sheet_write_chunk":
            return {"written": True}
        if action == "sheet_verify":
            raise GoogleSheetsBridgeError("verify failed", code="VERIFY_FAILED")
        if action == "sheet_abort":
            assert idempotency_key and idempotency_key.startswith("birzha:sheet_abort:")
            return {"aborted": True}
        raise AssertionError(action)

    monkeypatch.setattr(b, "_post", fake_post)
    with pytest.raises(GoogleSheetsBridgeError):
        b.replace_snapshot(
            spreadsheet_id="sheet-1",
            sheets={"D1": [["key", "date"], ["a", "2026-09-14"]]},
        )
    assert actions == ["sheet_stage_begin", "sheet_write_chunk", "sheet_verify", "sheet_abort"]
