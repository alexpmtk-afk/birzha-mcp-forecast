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


@pytest.mark.parametrize("symbol", ["Si", "BR", "GOLD", "IMOEX", "RI", "SBER"])
def test_ensure_archive_uses_fixed_id_without_path_search(monkeypatch, symbol):
    from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS
    b = bridge()
    target = INSTRUMENT_TARGETS[symbol.upper()]
    def fake_post(action, payload, **kwargs):
        assert action == "sheet_ensure"
        assert payload == {"spreadsheet_id": target.spreadsheet_id}
        return {"spreadsheet": {"id": target.spreadsheet_id, "name": target.spreadsheet_name}, "created": False}
    monkeypatch.setattr(b, "_post", fake_post)
    assert b.ensure_archive(symbol=symbol)["created"] is False


@pytest.mark.parametrize("fault", ["created", "id", "name", "missing_created"])
def test_ensure_archive_rejects_wrong_or_new_destination(monkeypatch, fault):
    from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS
    b = bridge(); target = INSTRUMENT_TARGETS["SI"]
    result = {"spreadsheet": {"id": target.spreadsheet_id, "name": target.spreadsheet_name}, "created": False}
    if fault == "created": result["created"] = True
    elif fault == "missing_created": del result["created"]
    else: result["spreadsheet"][fault] = "wrong"
    monkeypatch.setattr(b, "_post", lambda *a, **k: result)
    with pytest.raises(GoogleSheetsBridgeError): b.ensure_archive(symbol="Si")


def test_canonical_replacement_blocked_before_network(monkeypatch):
    from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS
    b = bridge()
    monkeypatch.setattr(b, "_post", lambda *a, **k: pytest.fail("no network"))
    with pytest.raises(GoogleSheetsBridgeError, match="append-only"):
        b.replace_snapshot(spreadsheet_id=INSTRUMENT_TARGETS["SI"].spreadsheet_id, sheets={"D1": [["key"], ["x"]]})


def test_unregistered_instrument_has_no_creation_path():
    for retired_or_unknown in ("TATN", "RTSI"):
        with pytest.raises(ValueError): bridge().ensure_archive(symbol=retired_or_unknown)


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
    assert all(key and key.startswith("birzha:v2:") for key in mutation_keys)


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
            assert idempotency_key and idempotency_key.startswith("birzha:v2:sheet_abort:")
            return {"aborted": True}
        raise AssertionError(action)

    monkeypatch.setattr(b, "_post", fake_post)
    with pytest.raises(GoogleSheetsBridgeError):
        b.replace_snapshot(
            spreadsheet_id="sheet-1",
            sheets={"D1": [["key", "date"], ["a", "2026-09-14"]]},
        )
    assert actions == ["sheet_stage_begin", "sheet_write_chunk", "sheet_verify", "sheet_abort"]



def test_mutation_replay_uses_deterministic_request_id_and_v2_namespace(monkeypatch):
    b = bridge()
    bodies: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            body = bodies[-1]
            return {
                "protocol_version": 1,
                "project_id": BRIDGE_PROJECT_ID,
                "request_id": body["request_id"],
                "action": body["action"],
                "ok": True,
                "result": {"committed": True},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers):
            bodies.append(dict(json))
            return FakeResponse()

    monkeypatch.setattr("birzha.storage.google_sheets_bridge.httpx.Client", FakeClient)
    key = b._stable_key("sheet_commit", "sheet-1", "D1", "digest")
    assert key.startswith("birzha:v2:sheet_commit:")
    assert b._post("sheet_commit", {"spreadsheet_id": "sheet-1"}, idempotency_key=key)["committed"] is True
    assert b._post("sheet_commit", {"spreadsheet_id": "sheet-1"}, idempotency_key=key)["committed"] is True
    assert len(bodies) == 2
    assert bodies[0]["request_id"] == bodies[1]["request_id"]
    assert bodies[0]["idempotency_key"] == bodies[1]["idempotency_key"] == key


def test_bridge_still_rejects_wrong_request_id(monkeypatch):
    b = bridge()

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {
                "protocol_version": 1,
                "project_id": BRIDGE_PROJECT_ID,
                "request_id": "definitely-not-the-request-id",
                "action": "sheet_commit",
                "ok": True,
                "result": {"committed": True},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers):
            return FakeResponse()

    monkeypatch.setattr("birzha.storage.google_sheets_bridge.httpx.Client", FakeClient)
    key = b._stable_key("sheet_commit", "sheet-1", "D1", "digest")
    with pytest.raises(GoogleSheetsBridgeError) as exc:
        b._post("sheet_commit", {"spreadsheet_id": "sheet-1"}, idempotency_key=key)
    assert exc.value.code == "REQUEST_ID_MISMATCH"
