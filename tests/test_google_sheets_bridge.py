from __future__ import annotations

import json

import pytest

from birzha.storage.google_sheets_bridge import (
    GoogleSheetsBridge,
    GoogleSheetsBridgeConfig,
    GoogleSheetsBridgeError,
    GoogleSheetsBridgeNotConfigured,
)


def _bridge(**overrides) -> GoogleSheetsBridge:
    values = {
        "bridge_url": "https://script.google.com/macros/s/test/exec",
        "bridge_secret": "secret",
        "root_folder_id": "root-id",
        "project_id": "birzha",
        "chunk_rows": 2,
    }
    values.update(overrides)
    return GoogleSheetsBridge(GoogleSheetsBridgeConfig(**values))


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses, captured, **kwargs) -> None:
        self.responses = responses
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, json, headers):
        self.captured.append(json)
        return self.responses.pop(0)


def _success(body: dict, result: dict | None = None) -> dict:
    return {
        "ok": True,
        "protocol_version": 1,
        "project_id": "birzha",
        "request_id": body["request_id"],
        "action": body["action"],
        "result": result or {},
    }


def test_project_id_is_pinned_to_birzha() -> None:
    with pytest.raises(GoogleSheetsBridgeNotConfigured, match="project_id"):
        _bridge(project_id="marketplaces")


def test_mutation_requires_idempotency_key_before_network() -> None:
    bridge = _bridge()
    with pytest.raises(ValueError, match="idempotency_key"):
        bridge._post("sheet_commit", {"x": 1})


def test_protocol_v1_envelope_contains_project_and_unique_request_ids(monkeypatch) -> None:
    bridge = _bridge()
    captured = []

    def client_factory(**kwargs):
        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, *, json, headers):
                captured.append(dict(json))
                return FakeResponse(200, _success(json, {"configured": True}))

        return Client()

    monkeypatch.setattr("birzha.storage.google_sheets_bridge.httpx.Client", client_factory)
    bridge._post("health")
    bridge._post("health")
    assert captured[0]["project_id"] == "birzha"
    assert captured[0]["action"] == "health"
    assert captured[0]["request_id"] != captured[1]["request_id"]
    assert "secret" in captured[0]


def test_retry_reuses_request_and_idempotency_ids(monkeypatch) -> None:
    bridge = _bridge(max_attempts=2)
    captured = []
    state = {"n": 0}

    def client_factory(**kwargs):
        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, *, json, headers):
                captured.append(dict(json))
                state["n"] += 1
                if state["n"] == 1:
                    return FakeResponse(503, {})
                return FakeResponse(200, _success(json, {"committed": True}))

        return Client()

    monkeypatch.setattr("birzha.storage.google_sheets_bridge.httpx.Client", client_factory)
    monkeypatch.setattr(bridge, "_backoff", lambda attempt: None)
    bridge._post("sheet_commit", {}, idempotency_key="stable-key")
    assert len(captured) == 2
    assert captured[0]["request_id"] == captured[1]["request_id"]
    assert captured[0]["idempotency_key"] == captured[1]["idempotency_key"] == "stable-key"


def test_request_id_mismatch_fails_closed(monkeypatch) -> None:
    bridge = _bridge()

    def client_factory(**kwargs):
        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, *, json, headers):
                payload = _success(json)
                payload["request_id"] = "wrong"
                return FakeResponse(200, payload)

        return Client()

    monkeypatch.setattr("birzha.storage.google_sheets_bridge.httpx.Client", client_factory)
    with pytest.raises(GoogleSheetsBridgeError, match="REQUEST_ID_MISMATCH"):
        bridge._post("health")


def test_deep_health_requires_v1_security_capabilities(monkeypatch) -> None:
    bridge = _bridge()
    monkeypatch.setattr(
        bridge,
        "_post",
        lambda action, *args, **kwargs: {
            "protocol_version": 1,
            "project_id": "birzha",
            "root_id": "root-id",
            "bridge_release": "1.0.0-alpha.1",
            "capabilities": {
                "google_sheets_chunked": True,
                "fixed_root_file_id_guard": True,
                "idempotent_mutations": True,
                "global_script_lock": False,
            },
        },
    )
    assert bridge.health()["project_id"] == "birzha"


def test_deep_health_rejects_wrong_root(monkeypatch) -> None:
    bridge = _bridge()
    monkeypatch.setattr(
        bridge,
        "_post",
        lambda action, *args, **kwargs: {
            "protocol_version": 1,
            "project_id": "birzha",
            "root_id": "wrong-root",
            "capabilities": {},
        },
    )
    with pytest.raises(GoogleSheetsBridgeError, match="ROOT_MISMATCH"):
        bridge.health()


def test_sheet_chunk_is_bounded_and_sends_stable_sha(monkeypatch) -> None:
    bridge = _bridge(chunk_rows=2)
    captured = {}

    def fake_post(action, payload=None, **kwargs):
        captured["action"] = action
        captured["payload"] = payload
        captured.update(kwargs)
        return {"written_rows": len(payload["values"])}

    monkeypatch.setattr(bridge, "_post", fake_post)
    rows = [["a", "b"], [1, 2]]
    bridge.sheet_write_chunk(
        spreadsheet_id="sheet",
        stage_sheet_title="stage",
        start_row=1,
        start_col=1,
        values=rows,
        idempotency_key="chunk-key",
    )
    assert captured["action"] == "sheet_write_chunk"
    assert captured["idempotency_key"] == "chunk-key"
    assert len(captured["payload"]["chunk_sha256"]) == 64
    assert captured["payload"]["values"] == rows

    with pytest.raises(ValueError, match="chunk_rows"):
        bridge.sheet_write_chunk(
            spreadsheet_id="sheet",
            stage_sheet_title="stage",
            start_row=1,
            start_col=1,
            values=[[1], [2], [3]],
            idempotency_key="too-large",
        )
