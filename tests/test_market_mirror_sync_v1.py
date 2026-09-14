from __future__ import annotations

from types import SimpleNamespace

import birzha.application.market_mirror_sync as module
from birzha.application.market_mirror_sync import MarketMirrorSyncService


def test_sync_commits_data_before_final_pass_status(monkeypatch):
    snapshot = SimpleNamespace(
        symbol="BR",
        from_date="2026-09-01",
        till_date="2026-09-14",
        data_rows=2,
        contract_count=1,
        sheets={
            "D1": [["key", "date"], ["a", "2026-09-01"], ["b", "2026-09-14"]],
            "SESSIONS": [["symbol", "secid", "trade_date"], ["BR", "BRV6", "2026-09-14"]],
            "VERIFIED_RANGES": [["scope", "symbol", "timeframe", "from", "till"], ["YDB_VERIFIED", "BR", "D1", "2026-09-01", "2026-09-14"]],
            "SYNC_STATUS": [["key", "value"], ["status", "SOURCE_READY_FOR_BRIDGE_PARITY"]],
        },
    )
    monkeypatch.setattr(module, "build_market_mirror_snapshot", lambda *args, **kwargs: snapshot)

    class FakeBridge:
        def __init__(self):
            self.calls: list[tuple[str, object]] = []

        def health(self):
            self.calls.append(("health", None))
            return {"bridge_release": "1.0.0"}

        def ensure_archive(self, *, symbol):
            self.calls.append(("ensure", symbol))
            return {"spreadsheet_id": "sheet-1", "spreadsheet_name": "BR", "folder_name": "BR — Brent", "created": False}

        def replace_snapshot(self, *, spreadsheet_id, sheets):
            self.calls.append(("replace", {k: [list(r) for r in v] for k, v in sheets.items()}))
            if set(sheets) == {"D1", "SESSIONS", "VERIFIED_RANGES"}:
                return {
                    "parity": True,
                    "sheets": {
                        "D1": {
                            "data_rows": 2,
                            "sha256": "a" * 64,
                        }
                    },
                }
            assert set(sheets) == {"SYNC_STATUS"}
            status_rows = sheets["SYNC_STATUS"]
            values = {str(row[0]): row[1] for row in status_rows[1:] if len(row) >= 2}
            assert values["status"] == "MIRROR_SYNC_PASS"
            assert values["bridge_protocol"] == 1
            assert values["bridge_release"] == "1.0.0"
            assert values["readback_row_count"] == 2
            assert values["d1_post_commit_sha256"] == "a" * 64
            return {"parity": True, "sheets": {"SYNC_STATUS": {"data_rows": len(status_rows) - 1}}}

        def summary(self, *, spreadsheet_id):
            self.calls.append(("summary", spreadsheet_id))
            return {
                "sheets": {
                    "D1": {"data_rows": 2, "sha256": "a" * 64},
                    "SYNC_STATUS": {"data_rows": 6, "sha256": "b" * 64},
                }
            }

    bridge = FakeBridge()
    result = MarketMirrorSyncService(source=object(), bridge=bridge).sync(
        symbol="BR", from_date="2026-09-01", till_date="2026-09-14"
    )

    assert result["status"] == "MIRROR_SYNC_PASS"
    assert result["bridge_release"] == "1.0.0"
    assert [name for name, _ in bridge.calls] == ["health", "ensure", "replace", "replace", "summary"]
    first_replace = bridge.calls[2][1]
    second_replace = bridge.calls[3][1]
    assert isinstance(first_replace, dict) and "SYNC_STATUS" not in first_replace
    assert isinstance(second_replace, dict) and set(second_replace) == {"SYNC_STATUS"}


def test_sync_never_writes_pass_status_when_data_commit_fails(monkeypatch):
    snapshot = SimpleNamespace(
        symbol="SI",
        from_date="2026-09-01",
        till_date="2026-09-14",
        data_rows=1,
        contract_count=1,
        sheets={
            "D1": [["key", "date"], ["a", "2026-09-14"]],
            "SESSIONS": [["symbol", "secid", "trade_date"], ["SI", "SiZ6", "2026-09-14"]],
            "VERIFIED_RANGES": [["scope", "symbol", "timeframe", "from", "till"], ["YDB_VERIFIED", "SI", "D1", "2026-09-01", "2026-09-14"]],
            "SYNC_STATUS": [["key", "value"], ["status", "SOURCE_READY_FOR_BRIDGE_PARITY"]],
        },
    )
    monkeypatch.setattr(module, "build_market_mirror_snapshot", lambda *args, **kwargs: snapshot)

    class FakeBridge:
        writes = 0
        def health(self): return {"bridge_release": "1.0.0"}
        def ensure_archive(self, *, symbol): return {"spreadsheet_id": "sheet-1"}
        def replace_snapshot(self, *, spreadsheet_id, sheets):
            self.writes += 1
            raise RuntimeError("data commit failed")
        def summary(self, *, spreadsheet_id): raise AssertionError("summary must not run")

    bridge = FakeBridge()
    try:
        MarketMirrorSyncService(source=object(), bridge=bridge).sync(
            symbol="SI", from_date="2026-09-01", till_date="2026-09-14"
        )
    except RuntimeError as exc:
        assert "data commit failed" in str(exc)
    else:
        raise AssertionError("sync must fail")
    assert bridge.writes == 1
