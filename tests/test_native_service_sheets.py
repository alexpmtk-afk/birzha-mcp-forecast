"""Exercise service-sheet commits through the real native transport, not a Book stub."""
from copy import deepcopy
import json
from urllib.parse import unquote

import httpx
import pytest

from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.home_market_mirror import HomeCanonicalMirror
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS, INSTRUMENT_FOLDER_IDS
from birzha.storage.historical_store import DuckDBHistoricalCandleStore
from birzha.storage.native_canonical_sheets import NativeCanonicalSheets


def api(derived=False):
    target = INSTRUMENT_TARGETS["SBER"]
    names = ["D1", "H1", "M15", "SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS"]
    if derived:
        names += ["INDICATORS", "FLOW", "POSITIONS"]
    ids = {name: index + 1 for index, name in enumerate(names)}
    state = {"rows": {name: [] for name in names}, "posts": [], "lose_service_response": False}
    state["rows"]["SYNC_STATUS"] = [["key", "value"], ["receipt", "keep"]]

    def values(cells):
        return [next(iter(cell["userEnteredValue"].values())) if "userEnteredValue" in cell else "" for cell in cells]

    def handler(request):
        assert target.spreadsheet_id in str(request.url)
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"id": target.spreadsheet_id, "name": target.spreadsheet_name,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [INSTRUMENT_FOLDER_IDS["SBER"]], "trashed": False})
        if request.method == "POST":
            body = json.loads(request.content)
            state["posts"].append(deepcopy(body))
            committed_service = False
            for operation in body["requests"]:
                if "appendCells" in operation:
                    arg = operation["appendCells"]
                    name = next(name for name, sid in ids.items() if sid == arg["sheetId"])
                    state["rows"][name].extend(values(row["values"]) for row in arg["rows"])
                    committed_service |= name in {"SESSIONS", "VERIFIED_RANGES"}
                else:
                    arg = operation["updateCells"]
                    name = next(name for name, sid in ids.items() if sid == arg["start"]["sheetId"])
                    for offset, row in enumerate(arg["rows"]):
                        state["rows"][name][arg["start"]["rowIndex"] + offset] = values(row["values"])
            if committed_service and state["lose_service_response"]:
                state["lose_service_response"] = False
                raise httpx.ReadTimeout("lost response after committed service append")
            return httpx.Response(200, json={"replies": []})
        if "/values/" in request.url.path:
            name = unquote(request.url.path.split("/values/")[1]).split("!")[0].strip("'")
            return httpx.Response(200, json={"values": state["rows"][name]})
        return httpx.Response(200, json={"spreadsheetId": target.spreadsheet_id, "sheets": [
            {"properties": {"sheetId": ids[name], "title": name, "sheetType": "GRID",
                "gridProperties": {"rowCount": 100, "columnCount": 40 if name in {"INDICATORS","FLOW","POSITIONS"} else 26}}} for name in names]})

    return NativeCanonicalSheets(lambda: "test-token", transport=httpx.MockTransport(handler)), state


@pytest.mark.parametrize("sheet,header,row", [
    ("SESSIONS", ["symbol", "secid", "trade_date"], ["SBER", "SBER", "2026-10-12"]),
    ("VERIFIED_RANGES", ["scope", "symbol", "timeframe", "from_date", "till_date"],
        ["research", "SBER", "D1", "2026-10-12", "2026-10-12"]),
    ("SYNC_STATUS", ["key", "value"], ["test", "value"]),
])
def test_service_append_refreshes_named_sheet_after_commit(sheet, header, row):
    transport, state = api()
    state["rows"][sheet] = [header]
    transport.append_service_rows("SBER", sheet, [row])
    assert transport.read_service_rows("SBER", sheet) == [header, row]
    assert len(state["posts"]) == 1
    with pytest.raises(MirrorConflict, match="unsupported timeframe"):
        transport.validate_target("SBER", sheet)


@pytest.mark.parametrize("lose_response", [False, True])
def test_full_native_mirror_service_readback_and_reconciled_resume(lose_response):
    db = DuckDBHistoricalCandleStore(":memory:")
    try:
        instrument = Instrument(symbol="SBER", secid="SBER", board="TQBR", engine="stock", market="shares", asset_class="equity")
        for tf, begin, end in [("D1", "00:00:00", "23:59:59"), ("H1", "10:00:00", "10:59:59"), ("M15", "10:00:00", "10:14:59")]:
            db.upsert_series(CandleSeries(instrument, tf, (Candle(open=100, high=102, low=99, close=101,
                volume=10, value=1000, begin="2026-10-12 " + begin, end="2026-10-12 " + end),)))
            db.mark_verified("SBER", tf, "2026-10-12", "2026-10-12")
        db.record_sessions("SBER", "SBER", ("2026-10-12",))
        source_before = db._connection.execute("SELECT * FROM historical_candles ORDER BY timeframe").fetchall()
        transport, state = api()
        service = HomeCanonicalMirror(db, transport, "a" * 40)
        kwargs = dict(symbol="SBER", from_date="2026-10-12", till_date="2026-10-12")
        assert service.sync(**kwargs)["planned_additions"] == {"D1": 1, "H1": 1, "M15": 1}
        assert state["posts"] == []
        state["lose_service_response"] = lose_response
        if lose_response:
            with pytest.raises(MirrorConflict, match="no automatic write retry"):
                service.sync(**kwargs, dry_run=False, exclusive_workbook_owner=True)
            assert dict(state["rows"]["SYNC_STATUS"][1:])["status"] == "MIRROR_SYNC_FAILED"
        result = service.sync(**kwargs, dry_run=False, exclusive_workbook_owner=True)
        assert result["status"] == "MIRROR_SYNC_PASS"
        snapshot = deepcopy(state["rows"])
        repeated = service.sync(**kwargs, dry_run=False, exclusive_workbook_owner=True)
        assert all(repeated["sheets"][tf]["added_rows"] == 0 for tf in ("D1", "H1", "M15"))
        assert all(state["rows"][name] == snapshot[name] for name in ("D1", "H1", "M15", "SESSIONS", "VERIFIED_RANGES"))
        assert len(state["rows"]["SESSIONS"]) == 2
        assert len(state["rows"]["VERIFIED_RANGES"]) == 4
        assert dict(state["rows"]["SYNC_STATUS"][1:])["receipt"] == "keep"
        assert db._connection.execute("SELECT * FROM historical_candles ORDER BY timeframe").fetchall() == source_before
    finally:
        db.close()
