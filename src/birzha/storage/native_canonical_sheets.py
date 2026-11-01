"""Native Google API transport for existing canonical sheets only.

Credentials are supplied by an authorised caller, never read from HOME or logs.
No create/copy/delete/clear/replace endpoint is implemented. No write retry.
"""
from __future__ import annotations

import json as json_module
import math
import time
from typing import Callable
from urllib.parse import quote

import httpx

from birzha.application.canonical_market_mirror import MAX_COLUMNS, MAX_ROWS, TIMEFRAMES, MirrorConflict
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge, INSTRUMENT_FOLDER_IDS


class NativeCanonicalSheets:
    def __init__(self, access_token: Callable[[], str], *, transport: httpx.BaseTransport | None = None):
        self._access_token = access_token
        self._transport = transport
        self._last_request = float("-inf")
        self._sheets: dict[tuple[str, str], dict] = {}

    def _request(self, method: str, url: str, *, params=None, json=None) -> dict:
        try:
            token = self._access_token()
        except Exception:
            raise MirrorConflict("Google authorisation provider failed") from None
        if not isinstance(token, str) or not token.strip():
            raise MirrorConflict("Google authorisation unavailable")
        # Sheets allows 60 read/write requests per minute per user/project.
        # This is a local pacing guard, not a reservation of shared quota.
        if not isinstance(self._transport, httpx.MockTransport):
            wait = 1.1 - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
        self._last_request = time.monotonic()
        try:
            with httpx.Client(transport=self._transport, timeout=60, follow_redirects=False) as client:
                response = client.request(method, url, params=params, json=json,
                                          headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            raise MirrorConflict("Google transport failed; no automatic write retry") from None
        if not response.is_success:
            raise MirrorConflict(f"Google request refused (HTTP {response.status_code}); no fallback")
        try:
            result = response.json()
        except ValueError:
            raise MirrorConflict("Google returned invalid JSON") from None
        if not isinstance(result, dict):
            raise MirrorConflict("Google response is not an object")
        return result

    def validate_target(self, symbol: str, timeframe: str) -> None:
        symbol, target = GoogleSheetsBridge.target_for_symbol(symbol)
        if timeframe not in TIMEFRAMES:
            raise MirrorConflict("unsupported timeframe")
        self._validate_named_target(symbol, timeframe)

    def _validate_named_target(self, symbol: str, timeframe: str) -> None:
        symbol, target = GoogleSheetsBridge.target_for_symbol(symbol)
        if timeframe not in TIMEFRAMES | {"SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS", "D1_INDICATORS", "H1_INDICATORS", "M15_INDICATORS"}:
            raise MirrorConflict("unsupported canonical sheet")
        meta = self._request("GET", f"https://www.googleapis.com/drive/v3/files/{target.spreadsheet_id}",
                             params={"fields": "id,name,mimeType,parents,trashed"})
        if (meta.get("id") != target.spreadsheet_id or meta.get("name") != target.spreadsheet_name
                or meta.get("mimeType") != "application/vnd.google-apps.spreadsheet"
                or meta.get("trashed") is not False
                or meta.get("parents") != [INSTRUMENT_FOLDER_IDS[symbol]]):
            raise MirrorConflict("canonical file identity, name or folder mismatch")
        book = self._request("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}",
                             params={"fields": "spreadsheetId,sheets.properties"})
        matches = [x["properties"] for x in book.get("sheets", []) if x.get("properties", {}).get("title") == timeframe]
        if book.get("spreadsheetId") != target.spreadsheet_id or len(matches) != 1:
            raise MirrorConflict("existing timeframe tab missing; no implicit creation")
        props = matches[0]
        grid = props.get("gridProperties", {})
        if props.get("sheetType", "GRID") != "GRID" or not 1 <= grid.get("rowCount", 0) <= MAX_ROWS or not 1 <= grid.get("columnCount", 0) <= (160 if timeframe.endswith("_INDICATORS") else MAX_COLUMNS):
            raise MirrorConflict("unsupported native grid bounds")
        self._sheets[(symbol, timeframe)] = props

    def read_rows(self, symbol: str, timeframe: str) -> list[list[object]]:
        symbol, target = GoogleSheetsBridge.target_for_symbol(symbol)
        props = self._sheets.get((symbol, timeframe))
        if props is None:
            raise MirrorConflict("validate destination before reading")
        grid = props["gridProperties"]
        count = grid["columnCount"]
        # A..AN (40): bounded native grid; column letters derived without library dependency.
        end_column = ""
        while count:
            count, remainder = divmod(count - 1, 26)
            end_column = chr(65 + remainder) + end_column
        result = []
        for offset in range(0, grid["rowCount"], 2000):
            range_name = f"'{timeframe}'!A{offset + 1}:{end_column}{min(offset + 2000, grid['rowCount'])}"
            values = self._request("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}/values/{quote(range_name, safe='')}",
                                   params={"valueRenderOption": "FORMULA", "dateTimeRenderOption": "SERIAL_NUMBER", "majorDimension": "ROWS"}).get("values", [])
            if not isinstance(values, list) or any(not isinstance(row, list) for row in values) or len(values) > 2000:
                raise MirrorConflict("invalid bounded read response")
            result.extend(values)
            result.extend([[] for _ in range(min(2000, grid["rowCount"] - offset) - len(values))])
        while result and not result[-1]:
            result.pop()
        return result

    def model3_read_window(self, symbol: str, sheet: str, start: int, stop: int, width: int) -> list[list[object]]:
        """Read literal/formula cells in a bounded existing-tab row window.

        Padding outside allocated grid is empty, never an invented market value.
        Used for per-chunk compare/readback; final whole-table read is separate.
        """
        symbol,target=GoogleSheetsBridge.target_for_symbol(symbol)
        props=self._sheets.get((symbol,sheet))
        if props is None or sheet not in {"D1","H1","M15","D1_INDICATORS","H1_INDICATORS","M15_INDICATORS"}:
            raise MirrorConflict("validate existing archive tab before window read")
        if not 0<=start<stop<=MAX_ROWS or stop-start>2000 or not 1<=width<=160:
            raise MirrorConflict("bounded read window required")
        grid=props["gridProperties"];count=min(width,grid["columnCount"]);letters=""
        while count:
            count,rem=divmod(count-1,26);letters=chr(65+rem)+letters
        values=[];last=min(stop,grid["rowCount"])
        if start<last:
            name=f"'{sheet}'!A{start+1}:{letters}{last}"
            values=self._request("GET",f"https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}/values/{quote(name,safe='')}",
                params={"valueRenderOption":"FORMULA","dateTimeRenderOption":"SERIAL_NUMBER","majorDimension":"ROWS"}).get("values",[])
        if not isinstance(values,list) or len(values)>stop-start or any(not isinstance(r,list) or len(r)>width for r in values):
            raise MirrorConflict("invalid bounded window response")
        return [r+[""]*(width-len(r)) for r in values]+[[""]*width for _ in range(stop-start-len(values))]

    def append_rows(self, symbol: str, timeframe: str, rows: list[list[object]]) -> None:
        symbol, target = GoogleSheetsBridge.target_for_symbol(symbol)
        if not rows or len(rows) > 1000:
            raise MirrorConflict("invalid append chunk")
        props = self._sheets.get((symbol, timeframe))
        if props is None:
            raise MirrorConflict("validate destination before writing")
        def cell(value):
            if value in (None, ""):
                return {}
            if isinstance(value, bool):
                return {"userEnteredValue": {"boolValue": value}}
            if isinstance(value, (int, float)):
                if not math.isfinite(value):
                    raise MirrorConflict("nonfinite append cell")
                return {"userEnteredValue": {"numberValue": value}}
            if isinstance(value, str) and not value.startswith("="):
                return {"userEnteredValue": {"stringValue": value}}
            raise MirrorConflict("unsupported literal cell")
        if any(len(row) > props["gridProperties"]["columnCount"] for row in rows):
            raise MirrorConflict("append exceeds existing column schema")
        body = {"requests": [{"appendCells": {"sheetId": props["sheetId"],
                "rows": [{"values": [cell(x) for x in row]} for row in rows],
                "fields": "userEnteredValue"}}]}
        if len(json_module.dumps(body, ensure_ascii=False, allow_nan=False).encode()) > 1_000_000:
            raise MirrorConflict("append payload exceeds 1 MB; reduce chunk size")
        self._request("POST", f"https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}:batchUpdate", json=body)
        # Refresh grid bounds: appendCells may expand the allocated sheet.
        self._validate_named_target(symbol, timeframe)

    def model3_insert_rows(self, symbol: str, sheet: str, row_index: int,
                           rows: list[list[object]]) -> None:
        """One atomic native insertion; never overwrite pre-existing indicator cells.

        A lost HTTP response has unknown outcome; callers MUST re-read all
        source keys before any subsequent attempt. No automatic write retry.
        """
        self.validate_model3_target(symbol, sheet)
        props = self._sheets[(symbol, sheet)]
        grid = props["gridProperties"]
        cols = grid["columnCount"]
        if sheet not in {"D1_INDICATORS", "H1_INDICATORS", "M15_INDICATORS"}:
            raise MirrorConflict("MODEL3_INSERT_INDICATORS_ONLY")
        if not isinstance(row_index, int) or isinstance(row_index, bool) or not 1 <= row_index <= grid["rowCount"]:
            raise MirrorConflict("MODEL3_INSERT_POSITION")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 50 or any(len(r) != cols for r in rows):
            raise MirrorConflict("MODEL3_INSERT_COMPACT_ROWS_ONLY")

        def literal(value):
            if value is None or value == "":
                return {}
            if isinstance(value, bool):
                return {"userEnteredValue": {"boolValue": value}}
            if isinstance(value, (float, int)) and math.isfinite(value):
                return {"userEnteredValue": {"numberValue": value}}
            if isinstance(value, str) and len(value) <= 45000 and not value.startswith("="):
                return {"userEnteredValue": {"stringValue": value}}
            raise MirrorConflict("MODEL3_INSERT_NONLITERAL_CELL")

        request = {"requests": [
            {"insertDimension": {
                "range": {"sheetId": props["sheetId"], "dimension": "ROWS",
                          "startIndex": row_index, "endIndex": row_index + len(rows)},
                "inheritFromBefore": row_index > 1}},
            {"updateCells": {
                "start": {"sheetId": props["sheetId"], "rowIndex": row_index, "columnIndex": 0},
                "rows": [{"values": [literal(x) for x in r]} for r in rows],
                "fields": "userEnteredValue"}},
        ]}
        if len(json_module.dumps(request, ensure_ascii=False, allow_nan=False).encode()) > 750_000:
            raise MirrorConflict("MODEL3_INSERT_PAYLOAD_LIMIT")
        symbol, target = GoogleSheetsBridge.target_for_symbol(symbol)
        self._request("POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}:batchUpdate",
            json=request)
        self._validate_named_target(symbol, sheet)

    def validate_service_target(self, symbol: str, sheet: str) -> None:
        if sheet not in {"SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS"}:
            raise MirrorConflict("unsupported service sheet")
        self._validate_named_target(symbol, sheet)

    def read_service_rows(self, symbol: str, sheet: str) -> list[list[object]]:
        rows = self.read_rows(symbol, sheet)
        if not rows:
            return []
        width = len(rows[0])
        if any(len(row) > width for row in rows):
            raise MirrorConflict("service sheet data exceeds header")
        return [row + [""] * (width - len(row)) for row in rows]

    def append_service_rows(self, symbol: str, sheet: str, rows: list[list[object]]) -> None:
        self.validate_service_target(symbol, sheet)
        self.append_rows(symbol, sheet, rows)

    def read_status(self, symbol: str) -> list[list[object]]:
        rows = self.read_service_rows(symbol,"SYNC_STATUS")
        active = [r for r in rows[1:] if any(x != "" for x in r)]
        if rows and (rows[0] != ["key","value"] or any(not isinstance(r[0],str) or not r[0] for r in active) or len({r[0] for r in active}) != len(active)):
            raise MirrorConflict("invalid or duplicate status keys")
        return rows

    def update_status(self, symbol: str, updates: dict[str,object]) -> None:
        from birzha.application.canonical_market_mirror import digest
        symbol,target = GoogleSheetsBridge.target_for_symbol(symbol)
        self.validate_service_target(symbol,"SYNC_STATUS")
        old=self.read_status(symbol)
        rows=[list(r) for r in old] if old else [["key","value"]]
        positions={row[0]:index for index,row in enumerate(rows[1:],1) if row[0] != ""}
        # Preserve superseded configuration as dated evidence before correcting it.
        for key in ("primary_store","persistent_timeframe","intraday_mode","sync_direction"):
            if key in updates and key in positions and rows[positions[key]][1] != updates[key]:
                legacy="legacy_pre_mirror_v3_"+key
                if legacy not in positions:
                    positions[legacy]=len(rows);rows.append([legacy,rows[positions[key]][1]])
        for key,value in updates.items():
            if not isinstance(key,str) or not key or not isinstance(value,(str,int,float,bool)):
                raise MirrorConflict("invalid status value")
            if key in positions: rows[positions[key]][1]=value
            else: positions[key]=len(rows);rows.append([key,value])
        if digest(self.read_status(symbol)) != digest(old):
            raise MirrorConflict("status changed after preflight")
        sheet_id=self._sheets[(symbol,"SYNC_STATUS")]["sheetId"]
        def cell(x):
            if isinstance(x,bool): return {"userEnteredValue":{"boolValue":x}}
            if isinstance(x,(float,int)):
                if not math.isfinite(x): raise MirrorConflict("invalid numeric status")
                return {"userEnteredValue":{"numberValue":x}}
            if isinstance(x,str) and not x.startswith("="): return {"userEnteredValue":{"stringValue":x}}
            raise MirrorConflict("invalid status literal")
        requests=[]
        for index,row in enumerate(rows[:len(old)]):
            if row != old[index]:
                requests.append({"updateCells":{"start":{"sheetId":sheet_id,"rowIndex":index,"columnIndex":0},"rows":[{"values":[cell(x) for x in row]}],"fields":"userEnteredValue"}})
        added=rows[len(old):]
        if added: requests.append({"appendCells":{"sheetId":sheet_id,"rows":[{"values":[cell(x) for x in row]} for row in added],"fields":"userEnteredValue"}})
        if requests:
            self._request("POST",f"https://sheets.googleapis.com/v4/spreadsheets/{target.spreadsheet_id}:batchUpdate",json={"requests":requests})
            self.validate_service_target(symbol,"SYNC_STATUS")
        if digest(self.read_status(symbol)) != digest(rows):
            raise MirrorConflict("status post-write readback mismatch")

    def validate_model3_target(self, symbol: str, sheet: str) -> None:
        if symbol != "RI" or sheet not in TIMEFRAMES | {"D1_INDICATORS", "H1_INDICATORS", "M15_INDICATORS"}:
            raise MirrorConflict("Model3 archive is limited to existing RI tabs")
        self._validate_named_target(symbol, sheet)

    def model3_write_cells(self, symbol: str, sheet: str, edits: list[tuple[int, int, object]]) -> None:
        """Literal missing-field/approved defect patch; no formula or value-only row replacement."""
        self.validate_model3_target(symbol, sheet)
        props=self._sheets[(symbol,sheet)]; grid=props["gridProperties"]
        if not edits or len(edits)>2000:raise MirrorConflict("bounded cell patch required")
        if any(not isinstance(r,int) or not isinstance(c,int) or r<0 or c<0 or r>=MAX_ROWS or c>=160 for r,c,v in edits):
            raise MirrorConflict("cell patch outside existing-tab bounds")
        def cell(v):
            if v is None or v=="":return {}
            if isinstance(v,bool):return {"userEnteredValue":{"boolValue":v}}
            if isinstance(v,(int,float)) and math.isfinite(v):return {"userEnteredValue":{"numberValue":v}}
            if isinstance(v,str) and len(v)<=45000 and not v.startswith("="):return {"userEnteredValue":{"stringValue":v}}
            raise MirrorConflict("unsupported literal patch")
        rows=max(grid["rowCount"],max(r for r,c,v in edits)+1);cols=max(grid["columnCount"],max(c for r,c,v in edits)+1)
        requests=[]
        if (rows,cols)!=(grid["rowCount"],grid["columnCount"]):
            requests.append({"updateSheetProperties":{"properties":{"sheetId":props["sheetId"],"gridProperties":{"rowCount":rows,"columnCount":cols}},"fields":"gridProperties.rowCount,gridProperties.columnCount"}})
        for r,c,v in edits:
            requests.append({"updateCells":{"start":{"sheetId":props["sheetId"],"rowIndex":r,"columnIndex":c},"rows":[{"values":[cell(v)]}],"fields":"userEnteredValue"}})
        body={"requests":requests}
        if len(json_module.dumps(body,ensure_ascii=False,allow_nan=False).encode())>650000:raise MirrorConflict("cell patch exceeds transfer bound")
        self._request("POST",f"https://sheets.googleapis.com/v4/spreadsheets/{GoogleSheetsBridge.target_for_symbol(symbol)[1].spreadsheet_id}:batchUpdate",json=body)
        self._validate_named_target(symbol,sheet)

    def model3_order_rows(self, symbol: str, sheet: str, permutation: list[int]) -> None:
        """Move complete native rows, including styles/notes; never rewrite source cells."""
        if sheet in TIMEFRAMES:self.validate_target(symbol,sheet)
        else:self.validate_model3_target(symbol,sheet)
        if len(permutation)>MAX_ROWS-1 or sorted(permutation)!=list(range(len(permutation))):
            raise MirrorConflict("invalid row permutation")
        props=self._sheets[(symbol,sheet)]
        rows=self.read_rows(symbol,sheet)
        from birzha.application.home_market_mirror import _stamp
        header=rows[0]
        time_col=header.index("begin" if sheet.endswith("_INDICATORS") else "bar_start_time")
        sec_col=header.index("secid" if sheet.endswith("_INDICATORS") else "contract_code")
        values=[row[time_col] for row in rows[1:]]
        if len(values)!=len(permutation):raise MirrorConflict("row count changed before ordering")
        # Fixed Moscow ISO strings have the same lexical and chronological order.
        # One native sort is much smaller than thousands of single-row moves.
        if values and all(isinstance(v,str) and v==_stamp(v).isoformat() for v in values):
            self._request("POST",f"https://sheets.googleapis.com/v4/spreadsheets/{GoogleSheetsBridge.target_for_symbol(symbol)[1].spreadsheet_id}:batchUpdate",
                json={"requests":[{"sortRange":{"range":{"sheetId":props["sheetId"],"startRowIndex":1,"endRowIndex":len(rows),"startColumnIndex":0,"endColumnIndex":props["gridProperties"]["columnCount"]},
                    "sortSpecs":[{"dimensionIndex":time_col,"sortOrder":"ASCENDING"},{"dimensionIndex":sec_col,"sortOrder":"ASCENDING"}]}}]})
            return
        current=list(range(len(permutation)));requests=[]
        for destination,wanted in enumerate(permutation):
            position=current.index(wanted,destination)
            if position==destination:continue
            end=position+1
            while end<len(current) and destination+end-position<len(permutation) and current[end]==permutation[destination+end-position]:
                end+=1
            requests.append({"moveDimension":{"source":{"sheetId":props["sheetId"],"dimension":"ROWS","startIndex":position+1,"endIndex":end+1},"destinationIndex":destination+1}})
            block=current[position:end];del current[position:end];current[destination:destination]=block
        if len(requests)>5000:raise MirrorConflict("row ordering operation bound")
        for offset in range(0,len(requests),200):
            self._request("POST",f"https://sheets.googleapis.com/v4/spreadsheets/{GoogleSheetsBridge.target_for_symbol(symbol)[1].spreadsheet_id}:batchUpdate",json={"requests":requests[offset:offset+200]})
