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
        if timeframe not in TIMEFRAMES | {"SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS", "INDICATORS", "FLOW", "POSITIONS"}:
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
        if props.get("sheetType", "GRID") != "GRID" or not 1 <= grid.get("rowCount", 0) <= MAX_ROWS or not 1 <= grid.get("columnCount", 0) <= MAX_COLUMNS:
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
        end_column = chr(64 + count) if count <= 26 else "A" + chr(64 + count - 26)
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
        # Both candle and service appends may expand the allocated sheet.
        # Keep the public candle-only validator strict; refresh the named target.
        self._validate_named_target(symbol, timeframe)

    def validate_derived_target(self, symbol: str, sheet: str) -> None:
        if sheet not in {"INDICATORS", "FLOW", "POSITIONS"}:
            raise MirrorConflict("unsupported derived sheet")
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
