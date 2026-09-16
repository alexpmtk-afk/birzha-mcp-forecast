from __future__ import annotations

from pathlib import Path


SRC = Path("src/birzha/storage/google_sheets_bridge.py")
TEST = Path("tests/test_google_sheets_bridge_v1.py")


def main() -> None:
    text = SRC.read_text()
    old_key = '''    @staticmethod
    def _stable_key(action: str, *parts: object) -> str:
        material = "\\x00".join(str(part) for part in parts)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"birzha:{action}:{digest}"
'''
    new_key = '''    @staticmethod
    def _stable_key(action: str, *parts: object) -> str:
        material = "\\x00".join(str(part) for part in parts)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        # v2 deliberately rotates the Apps Script idempotency cache namespace.
        # Older cached mutation envelopes contain request_ids generated randomly
        # by the v1 client and cannot be safely replay-correlated.
        return f"birzha:v2:{action}:{digest}"
'''
    if old_key not in text:
        raise RuntimeError("stable-key source shape changed; refusing blind patch")
    text = text.replace(old_key, new_key, 1)

    old_request = '''        request_id = str(uuid.uuid4())
        body: dict[str, Any] = {
'''
    new_request = '''        if idempotency_key:
            # A mutation replayed under the same semantic idempotency key must
            # carry the same correlation id. The bridge may replay the original
            # response envelope; deterministic correlation keeps the strict
            # request_id equality check valid across process restarts.
            request_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{BRIDGE_PROJECT_ID}:{action}:{idempotency_key}",
                )
            )
        else:
            request_id = str(uuid.uuid4())
        body: dict[str, Any] = {
'''
    if old_request not in text:
        raise RuntimeError("request-id source shape changed; refusing blind patch")
    text = text.replace(old_request, new_request, 1)
    SRC.write_text(text)

    tests = TEST.read_text()
    tests = tests.replace(
        'startswith("birzha:sheet_ensure:")',
        'startswith("birzha:v2:sheet_ensure:")',
    )
    tests = tests.replace(
        'startswith("birzha:sheet_abort:")',
        'startswith("birzha:v2:sheet_abort:")',
    )
    tests = tests.replace(
        'startswith("birzha:") for key in mutation_keys',
        'startswith("birzha:v2:") for key in mutation_keys',
    )
    marker = "def test_mutation_replay_uses_deterministic_request_id_and_v2_namespace"
    if marker in tests:
        raise RuntimeError("regression tests already present")
    tests += '''


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
'''
    TEST.write_text(tests)
    print("M26_8_PATCH_APPLIED=PASS")


if __name__ == "__main__":
    main()
