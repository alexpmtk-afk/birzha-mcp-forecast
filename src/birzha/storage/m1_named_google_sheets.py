"""M1 v2.3 header-resolved Google Sheets I/O; canonical read-only, test-copy writes.

Not wired to Forecast Engine, MCP, existing NativeCanonicalSheets write paths,
or HOME. Requires caller-provided NativeCanonicalSheets transport credentials.
Mutation is intentionally **refused** on every canonical workbook ID.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

from birzha.application.m1_named_fields import (
    SchemaError, NamedSchema, api_column_from_index, compare_backread,
    read_named_row, resolve_header,
)
from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS
from birzha.storage.native_canonical_sheets import NativeCanonicalSheets

CANONICAL_IDS = frozenset(t.spreadsheet_id for t in INSTRUMENT_TARGETS.values())
MAX_RESEARCH_ROWS = 50000
MAX_RESEARCH_COLUMNS = 80
MAX_TEST_PATCH_CELLS = 24
TEST_PATCH_FIELDS = frozenset((
    "indicator_status", "atr_14_wilder_status", "quality_status", "calendar_status",
))


@dataclass(frozen=True)
class NamedSheetSnapshot:
    schema: NamedSchema
    rows: tuple[tuple[object, ...], ...]
    sheet_id: int
    physical_rows: tuple[int, ...]

    def records(self) -> tuple[dict[str, object], ...]:
        return tuple(read_named_row(self.schema, row) for row in self.rows)


class M1NamedGoogleSheets:
    """Real Google API read and guarded isolated-copy status patch via header map.

    Caller must authorize all credentials and external operations. This adapter
    refuses every canonical workbook for *writes*, including IDs reached under
    different labels. It does not solve concurrent external writes/transactionality.
    """

    def __init__(
        self, transport: NativeCanonicalSheets, *, spreadsheet_id: str,
        expected_title: str, sheet: str,
    ):
        if not isinstance(transport, NativeCanonicalSheets):
            raise SchemaError("SCHEMA_ERROR: validated NativeCanonicalSheets client required")
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id.strip():
            raise SchemaError("SCHEMA_ERROR: spreadsheet id required")
        if not isinstance(expected_title, str) or not expected_title.strip():
            raise SchemaError("SCHEMA_ERROR: exact spreadsheet title required")
        if sheet not in ("D1_INDICATORS", "H1_INDICATORS", "M15_INDICATORS"):
            raise SchemaError("SCHEMA_ERROR: only canonical indicator tab names allowed")
        self.transport = transport
        self.spreadsheet_id = spreadsheet_id
        self.expected_title = expected_title
        self.sheet = sheet

    def _validated_grid(self) -> tuple[int, int, int]:
        ident = self.transport._request(
            "GET", f"https://www.googleapis.com/drive/v3/files/{self.spreadsheet_id}",
            params={"fields": "id,name,mimeType,trashed"},
        )
        if not (ident.get("id") == self.spreadsheet_id and
                ident.get("name") == self.expected_title and
                ident.get("mimeType") == "application/vnd.google-apps.spreadsheet" and
                ident.get("trashed") is False):
            raise SchemaError("SCHEMA_ERROR: file identity/name/type mismatch")
        book = self.transport._request(
            "GET", f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}",
            params={"fields": "spreadsheetId,sheets.properties"},
        )
        tabs = [x["properties"] for x in book.get("sheets", [])
                if x.get("properties", {}).get("title") == self.sheet]
        if book.get("spreadsheetId") != self.spreadsheet_id or len(tabs) != 1:
            raise SchemaError("SCHEMA_ERROR: missing or duplicate named sheet")
        p = tabs[0]
        grid = p.get("gridProperties", {})
        row_count = grid.get("rowCount")
        col_count = grid.get("columnCount")
        if (type(p.get("sheetId")) is not int or
                p.get("sheetType", "GRID") != "GRID" or
                type(row_count) is not int or not 1 <= row_count <= MAX_RESEARCH_ROWS or
                type(col_count) is not int or not 1 <= col_count <= MAX_RESEARCH_COLUMNS):
            raise SchemaError("SCHEMA_ERROR: invalid grid size")
        return p["sheetId"], row_count, col_count

    def _get_values(self, address: str) -> list[list[object]]:
        data = self.transport._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
            f"/values/{quote(address, safe='')}",
            params={"valueRenderOption": "FORMULA", "dateTimeRenderOption": "SERIAL_NUMBER",
                    "majorDimension": "ROWS"},
        ).get("values", [])
        if not isinstance(data, list) or any(not isinstance(row, list) for row in data):
            raise SchemaError("SCHEMA_ERROR: invalid values response")
        return data

    def read(self) -> NamedSheetSnapshot:
        sheet_id, row_count, col_count = self._validated_grid()
        last = api_column_from_index(col_count - 1)
        quoted = f"'{self.sheet}'"
        header_result = self._get_values(f"{quoted}!A1:{last}1")
        if len(header_result) != 1:
            raise SchemaError("SCHEMA_ERROR: missing exact header row")
        schema = resolve_header(header_result[0], kind="indicators")
        if len(schema.fields) > col_count:
            raise SchemaError("SCHEMA_ERROR: header exceeds allocated grid")
        rows: list[tuple[object, ...]] = []
        physical_rows: list[int] = []
        for first in range(2, row_count + 1, 2000):
            last_row = min(first + 1999, row_count)
            block = self._get_values(f"{quoted}!A{first}:{last}{last_row}")
            if len(block) > last_row-first+1:
                raise SchemaError("SCHEMA_ERROR: oversized read response")
            for offset, row in enumerate(block):
                parsed = read_named_row(schema, row)
                if any(parsed.values()):
                    rows.append(tuple(parsed[field] for field in schema.fields))
                    physical_rows.append(first + offset - 1)
            # A Google value read may omit empty trailing rows; do not synthesize.
        return NamedSheetSnapshot(schema, tuple(rows), sheet_id, tuple(physical_rows))

    def patch_test_copy(
        self, patches: Mapping[str, Mapping[str, object]], *, isolated_copy_approved: bool,
    ) -> dict[str, object]:
        """Patch only permitted status fields, existing keys and an isolated copy.

        Explicit request, before/after snapshot, no retry on lost response,
        whole-sheet key/content backread. No row insertion or formula rewrite.
        """
        if isolated_copy_approved is not True:
            raise SchemaError("SCHEMA_ERROR: explicit test-copy approval required")
        if self.spreadsheet_id in CANONICAL_IDS:
            raise SchemaError("SCHEMA_ERROR: CANONICAL_WRITE_FORBIDDEN")
        if (not isinstance(patches, Mapping) or not patches or
                len(patches) > MAX_TEST_PATCH_CELLS):
            raise SchemaError("SCHEMA_ERROR: invalid bounded patch")
        before = self.read()
        positions: dict[str, tuple[int, dict[str, object]]] = {}
        for idx, record in enumerate(before.records(), start=1):
            k = record["record_key"]
            if not isinstance(k, str) or not k.strip() or k in positions:
                raise SchemaError("SCHEMA_ERROR: empty/duplicate existing record_key")
            positions[k] = (idx, record)
        requests: list[dict] = []
        expected = [dict(row) for row in before.records()]
        for key, fields in patches.items():
            if not isinstance(key, str) or key not in positions:
                raise SchemaError("SCHEMA_ERROR: target key missing")
            if not isinstance(fields, Mapping) or not fields or set(fields)-TEST_PATCH_FIELDS:
                raise SchemaError("SCHEMA_ERROR: only explicitly allowed status fields")
            idx, prior = positions[key]
            physical_row = before.physical_rows[idx-1]
            for field, value in fields.items():
                if not isinstance(value, str) or not value or value.startswith("=") or len(value)>512:
                    raise SchemaError("SCHEMA_ERROR: only bounded literal status")
                if str(prior[field]).startswith("="):
                    raise SchemaError("SCHEMA_ERROR: would overwrite formula")
                if prior[field] == value:
                    continue
                col = before.schema.position(field)
                requests.append({"updateCells": {
                    "start": {"sheetId": before.sheet_id, "rowIndex": physical_row, "columnIndex": col},
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": value}}]}],
                    "fields": "userEnteredValue",
                }})
                expected[idx-1][field] = value
        if len(requests) > MAX_TEST_PATCH_CELLS:
            raise SchemaError("SCHEMA_ERROR: patch too wide")
        if not requests:
            return {"status": "NO_CHANGE", "updated_cells": 0}
        precommit = self.read()
        if precommit != before:
            raise SchemaError("SCHEMA_ERROR: sheet changed after preflight")
        self.transport._request(
            "POST", f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
            json={"requests": requests},
        )  # Deliberately no automatic retry after uncertain response.
        after = self.read()
        if after.schema.fields != before.schema.fields or len(after.rows) != len(before.rows):
            raise SchemaError("SCHEMA_ERROR: backread changed sheet dimensions")
        compare_backread(before.schema, expected, after.schema.fields, after.rows)
        return {"status": "TEST_COPY_BACKREAD_PASS", "updated_cells": len(requests)}
