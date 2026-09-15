from datetime import date

from birzha.application.orchestration_plan import build_d1_archive_refresh_actions


def _days(left: str, right: str) -> int:
    return (date.fromisoformat(right) - date.fromisoformat(left)).days + 1


def test_futures_roots_use_shorter_d1_archive_chunks_than_direct_markets() -> None:
    actions = build_d1_archive_refresh_actions(
        "wf-test",
        "2024-01-01",
        "2025-01-31",
        "deadbeef",
    )
    sync = [a for a in actions if a.kind == "HISTORY_SYNC_CHUNK"]

    sber = [a for a in sync if a.payload["symbol"] == "SBER"]
    si = [a for a in sync if a.payload["symbol"] == "Si"]
    br = [a for a in sync if a.payload["symbol"] == "BR"]
    gold = [a for a in sync if a.payload["symbol"] == "GOLD"]

    assert max(_days(str(a.payload["from_date"]), str(a.payload["till_date"])) for a in sber) <= 366
    for root_actions in (si, br, gold):
        assert root_actions
        assert max(
            _days(str(a.payload["from_date"]), str(a.payload["till_date"]))
            for a in root_actions
        ) <= 92


def test_each_market_finalizer_references_exactly_its_own_chunks() -> None:
    actions = build_d1_archive_refresh_actions(
        "wf-test",
        "2024-01-01",
        "2024-12-31",
        "deadbeef",
    )
    for symbol in ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"):
        chunks = [
            [a.payload["from_date"], a.payload["till_date"]]
            for a in actions
            if a.kind == "HISTORY_SYNC_CHUNK" and a.payload["symbol"] == symbol
        ]
        finalizers = [
            a for a in actions
            if a.kind == "HISTORY_FINALIZE_RANGE" and a.payload["symbol"] == symbol
        ]
        assert len(finalizers) == 1
        assert finalizers[0].payload["chunks"] == chunks
