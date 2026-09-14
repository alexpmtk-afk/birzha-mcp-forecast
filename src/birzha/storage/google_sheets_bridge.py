"""Project-isolated Birzha client for Google Drive Bridge Protocol v1.

This module contains no Marketplaces dependency and no legacy protocol path.
YDB remains the primary data store; Google Sheets is the mandatory auditable
mirror when configured as required by the autonomous worker.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx


BRIDGE_PROTOCOL_VERSION = 1
BRIDGE_RELEASE = "1.0.0"
BRIDGE_PROJECT_ID = "birzha"
ALLOWED_SHEETS = frozenset({"D1", "SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS"})
MUTATING_ACTIONS = frozenset({
    "sheet_ensure",
    "sheet_stage_begin",
    "sheet_write_chunk",
    "sheet_commit",
    "sheet_abort",
})
MAX_ROWS_PER_SHEET = 25_000
MAX_COLUMNS = 40
MAX_TOTAL_CELLS = 500_000
DEFAULT_CHUNK_ROWS = 500


@dataclass(frozen=True, slots=True)
class InstrumentArchiveTarget:
    folder_name: str
    archive_code: str
    spreadsheet_id: str | None = None
    spreadsheet_name: str | None = None


INSTRUMENT_TARGETS: dict[str, InstrumentArchiveTarget] = {
    "SI": InstrumentArchiveTarget("Si — Доллар-рубль", "Si"),
    "BR": InstrumentArchiveTarget(
        "BR — Brent",
        "BR",
        spreadsheet_id="1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ",
        spreadsheet_name="BIRZHA — BR — Market Data Mirror",
    ),
    "GOLD": InstrumentArchiveTarget("GOLD — Золото", "GOLD"),
    "IMOEX": InstrumentArchiveTarget("MIX_MX — Индекс МосБиржи", "MIX_MX"),
    "RTSI": InstrumentArchiveTarget("RTS — Индекс РТС", "RTS"),
    "SBER": InstrumentArchiveTarget("SBER — Сбербанк", "SBER"),
    "TATN": InstrumentArchiveTarget("TATN — Татнефть", "TATN"),
}


class GoogleSheetsBridgeNotConfigured(RuntimeError):
    """Bridge v1 configuration is incomplete or invalid."""


class GoogleSheetsBridgeError(RuntimeError):
    """Bridge v1 rejected an operation or parity validation failed."""

    def __init__(self, message: str, *, code: str | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = str(code or "").strip() or None
        self.retryable = bool(retryable)


@dataclass(frozen=True, slots=True)
class GoogleSheetsBridgeConfig:
    bridge_url: str
    bridge_secret: str
    root_folder_id: str
    timeout_seconds: float = 120.0
    chunk_rows: int = DEFAULT_CHUNK_ROWS

    def __post_init__(self) -> None:
        if not self.bridge_url.startswith("https://script.google.com/macros/s/"):
            raise GoogleSheetsBridgeNotConfigured("Google Sheets Bridge v1 URL is invalid")
        if not self.bridge_secret.strip():
            raise GoogleSheetsBridgeNotConfigured("Google Sheets Bridge v1 secret is empty")
        if not self.root_folder_id.strip():
            raise GoogleSheetsBridgeNotConfigured("Google Sheets Bridge v1 root id is empty")
        if self.timeout_seconds <= 0:
            raise GoogleSheetsBridgeNotConfigured("Google Sheets Bridge v1 timeout must be positive")
        if self.chunk_rows < 1 or self.chunk_rows > 2_000:
            raise GoogleSheetsBridgeNotConfigured("Google Sheets Bridge v1 chunk_rows is outside 1..2000")


class GoogleSheetsBridge:
    """Synchronous fail-closed client for the stable shared Bridge v1 contract."""

    _MAX_ATTEMPTS = 3
    _RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}

    def __init__(self, config: GoogleSheetsBridgeConfig) -> None:
        self.config = config

    @staticmethod
    def _stable_key(action: str, *parts: object) -> str:
        material = "\x00".join(str(part) for part in parts)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"birzha:{action}:{digest}"

    @staticmethod
    def _rows_sha(rows: Sequence[Sequence[Any]]) -> str:
        payload = json.dumps(
            [list(row) for row in rows],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _post(
        self,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        action = str(action).strip().lower()
        if action in MUTATING_ACTIONS and not str(idempotency_key or "").strip():
            raise GoogleSheetsBridgeError(
                f"Bridge v1 mutation {action} requires idempotency_key",
                code="IDEMPOTENCY_KEY_REQUIRED",
            )
        request_id = str(uuid.uuid4())
        body: dict[str, Any] = {
            "secret": self.config.bridge_secret,
            "project_id": BRIDGE_PROJECT_ID,
            "request_id": request_id,
            "action": action,
            "payload": dict(payload or {}),
        }
        if idempotency_key:
            body["idempotency_key"] = str(idempotency_key)

        last_transport: Exception | None = None
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=True) as client:
                    response = client.post(
                        self.config.bridge_url,
                        json=body,
                        headers={"Accept": "application/json"},
                    )
            except httpx.HTTPError as exc:
                last_transport = exc
                if attempt < self._MAX_ATTEMPTS:
                    time.sleep(0.5 * attempt)
                    continue
                raise GoogleSheetsBridgeError(
                    f"Bridge v1 transport failed after {attempt} attempts: {type(exc).__name__}",
                    code="TRANSPORT_ERROR",
                    retryable=True,
                ) from exc

            if response.status_code in self._RETRYABLE_HTTP and attempt < self._MAX_ATTEMPTS:
                time.sleep(0.5 * attempt)
                continue
            if not response.is_success:
                raise GoogleSheetsBridgeError(
                    f"Bridge v1 HTTP {response.status_code}",
                    code="HTTP_ERROR",
                    retryable=response.status_code in self._RETRYABLE_HTTP,
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise GoogleSheetsBridgeError(
                    "Bridge v1 returned non-JSON",
                    code="NON_JSON_RESPONSE",
                ) from exc
            if not isinstance(data, dict):
                raise GoogleSheetsBridgeError("Bridge v1 response is not an object", code="INVALID_RESPONSE")
            if int(data.get("protocol_version") or 0) != BRIDGE_PROTOCOL_VERSION:
                raise GoogleSheetsBridgeError("Bridge protocol mismatch", code="PROTOCOL_MISMATCH")
            if str(data.get("project_id") or "") != BRIDGE_PROJECT_ID:
                raise GoogleSheetsBridgeError("Bridge project mismatch", code="PROJECT_MISMATCH")
            if str(data.get("request_id") or "") != request_id:
                raise GoogleSheetsBridgeError("Bridge request_id mismatch", code="REQUEST_ID_MISMATCH")
            echoed_action = str(data.get("action") or "")
            if echoed_action and echoed_action != action:
                raise GoogleSheetsBridgeError("Bridge action mismatch", code="ACTION_MISMATCH")
            if data.get("ok") is True:
                result = data.get("result")
                return dict(result) if isinstance(result, dict) else {}

            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            code = str(err.get("code") or "BRIDGE_REJECTED")
            retryable = bool(err.get("retryable"))
            message = str(err.get("message") or "bridge rejected request")
            if retryable and attempt < self._MAX_ATTEMPTS:
                time.sleep(0.5 * attempt)
                continue
            raise GoogleSheetsBridgeError(
                f"Bridge v1 {code}: {message}",
                code=code,
                retryable=retryable,
            )

        raise GoogleSheetsBridgeError(
            f"Bridge v1 transport failed: {type(last_transport).__name__ if last_transport else 'unknown'}",
            code="TRANSPORT_ERROR",
            retryable=True,
        )

    def health(self) -> dict[str, Any]:
        result = self._post("health")
        if int(result.get("protocol_version") or 0) != BRIDGE_PROTOCOL_VERSION:
            raise GoogleSheetsBridgeError("Bridge health protocol mismatch", code="PROTOCOL_MISMATCH")
        if str(result.get("bridge_release") or "") != BRIDGE_RELEASE:
            raise GoogleSheetsBridgeError(
                f"Bridge release mismatch: {result.get('bridge_release')!r} != {BRIDGE_RELEASE!r}",
                code="BRIDGE_RELEASE_MISMATCH",
            )
        if str(result.get("project_id") or "") != BRIDGE_PROJECT_ID:
            raise GoogleSheetsBridgeError("Bridge health project mismatch", code="PROJECT_MISMATCH")
        if str(result.get("root_id") or "") != self.config.root_folder_id:
            raise GoogleSheetsBridgeError("Bridge health root mismatch", code="ROOT_MISMATCH")
        caps = dict(result.get("capabilities") or {})
        for required in (
            "google_sheets_chunked",
            "google_sheets_inspect",
            "fixed_root_file_id_guard",
            "idempotent_mutations",
        ):
            if caps.get(required) is not True:
                raise GoogleSheetsBridgeError(
                    f"Bridge health capability {required} is required",
                    code="CAPABILITY_REQUIRED",
                )
        if caps.get("global_script_lock") is not False:
            raise GoogleSheetsBridgeError("Global ScriptLock must be disabled", code="GLOBAL_LOCK_FORBIDDEN")
        return result

    @staticmethod
    def target_for_symbol(symbol: str) -> tuple[str, InstrumentArchiveTarget]:
        key = str(symbol or "").strip().upper()
        if not key:
            raise ValueError("symbol is required")
        target = INSTRUMENT_TARGETS.get(key)
        if target is None:
            raise ValueError(f"unsupported Birzha archive symbol: {key}")
        return key, target

    def ensure_archive(self, *, symbol: str) -> dict[str, Any]:
        key, target = self.target_for_symbol(symbol)
        canonical_name = target.spreadsheet_name or f"MOEX_HISTDATA_{target.archive_code}_MCP_CANONICAL"
        payload: dict[str, Any]
        if target.spreadsheet_id:
            payload = {"spreadsheet_id": target.spreadsheet_id}
        else:
            payload = {"path": target.folder_name, "filename": canonical_name}
        result = self._post(
            "sheet_ensure",
            payload,
            idempotency_key=self._stable_key(
                "sheet_ensure",
                key,
                target.folder_name,
                canonical_name,
                target.spreadsheet_id or "",
            ),
        )
        meta = dict(result.get("spreadsheet") or {})
        spreadsheet_id = str(meta.get("id") or "").strip()
        if not spreadsheet_id:
            raise GoogleSheetsBridgeError("sheet_ensure returned no spreadsheet id", code="SPREADSHEET_ID_MISSING")
        if target.spreadsheet_id and spreadsheet_id != target.spreadsheet_id:
            raise GoogleSheetsBridgeError("canonical spreadsheet id mismatch", code="SPREADSHEET_ID_MISMATCH")
        if target.spreadsheet_name and str(meta.get("name") or "") != target.spreadsheet_name:
            raise GoogleSheetsBridgeError("canonical spreadsheet name mismatch", code="SPREADSHEET_NAME_MISMATCH")
        return {
            "symbol": key,
            "archive_code": target.archive_code,
            "folder_name": target.folder_name,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": str(meta.get("name") or canonical_name),
            "created": bool(result.get("created")),
        }

    @staticmethod
    def _validated_sheets(
        sheets: Mapping[str, Sequence[Sequence[Any]]],
    ) -> dict[str, list[list[Any]]]:
        if not sheets:
            raise ValueError("mirror snapshot must contain at least one sheet")
        normalized: dict[str, list[list[Any]]] = {}
        total_cells = 0
        for name, source_rows in sheets.items():
            if name not in ALLOWED_SHEETS:
                raise ValueError(f"unsupported mirror sheet: {name}")
            rows = [list(row) for row in source_rows]
            if not rows:
                raise ValueError(f"mirror sheet {name} has no header")
            width = len(rows[0])
            if width < 1 or width > MAX_COLUMNS:
                raise ValueError(f"mirror sheet {name} has invalid width {width}")
            if len(rows) > MAX_ROWS_PER_SHEET:
                raise ValueError(f"mirror sheet {name} exceeds {MAX_ROWS_PER_SHEET} rows")
            if any(len(row) != width for row in rows):
                raise ValueError(f"mirror sheet {name} is not rectangular")
            total_cells += len(rows) * width
            normalized[name] = rows
        if total_cells > MAX_TOTAL_CELLS:
            raise ValueError(f"mirror snapshot exceeds {MAX_TOTAL_CELLS} cells")
        return normalized

    @staticmethod
    def _date_column(sheet_name: str) -> int:
        return {"D1": 2, "SESSIONS": 3, "VERIFIED_RANGES": 4}.get(sheet_name, 0)

    def replace_snapshot(
        self,
        *,
        spreadsheet_id: str,
        sheets: Mapping[str, Sequence[Sequence[Any]]],
    ) -> dict[str, Any]:
        spreadsheet_id = str(spreadsheet_id or "").strip()
        if not spreadsheet_id:
            raise ValueError("spreadsheet_id is required")
        normalized = self._validated_sheets(sheets)
        summaries: dict[str, dict[str, Any]] = {}

        order = [name for name in ("D1", "SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS") if name in normalized]
        for sheet_name in order:
            rows = normalized[sheet_name]
            rows_sha = self._rows_sha(rows)
            stage_key = self._stable_key("sheet_stage_begin", spreadsheet_id, sheet_name, rows_sha)
            stage = self._post(
                "sheet_stage_begin",
                {"spreadsheet_id": spreadsheet_id, "sheet_title": sheet_name},
                idempotency_key=stage_key,
            )
            stage_title = str(stage.get("stage_sheet_title") or "").strip()
            if not stage_title:
                raise GoogleSheetsBridgeError("sheet_stage_begin returned no stage title", code="STAGE_TITLE_MISSING")
            committed = False
            try:
                for offset in range(0, len(rows), self.config.chunk_rows):
                    chunk = rows[offset : offset + self.config.chunk_rows]
                    chunk_sha = self._rows_sha(chunk)
                    self._post(
                        "sheet_write_chunk",
                        {
                            "spreadsheet_id": spreadsheet_id,
                            "stage_sheet_title": stage_title,
                            "start_row": offset + 1,
                            "start_col": 1,
                            "values": chunk,
                        },
                        idempotency_key=self._stable_key(
                            "sheet_write_chunk",
                            spreadsheet_id,
                            sheet_name,
                            rows_sha,
                            offset + 1,
                            chunk_sha,
                        ),
                    )

                verified = self._post(
                    "sheet_verify",
                    {
                        "spreadsheet_id": spreadsheet_id,
                        "stage_sheet_title": stage_title,
                        "date_column": self._date_column(sheet_name),
                    },
                )
                if int(verified.get("row_count") or -1) != len(rows):
                    raise GoogleSheetsBridgeError(
                        f"stage row-count mismatch for {sheet_name}",
                        code="ROW_COUNT_MISMATCH",
                    )
                if int(verified.get("column_count") or -1) != len(rows[0]):
                    raise GoogleSheetsBridgeError(
                        f"stage column-count mismatch for {sheet_name}",
                        code="COLUMN_COUNT_MISMATCH",
                    )
                digest = str(verified.get("digest") or "").strip().lower()
                if len(digest) != 64:
                    raise GoogleSheetsBridgeError(
                        f"stage digest missing for {sheet_name}",
                        code="DIGEST_MISSING",
                    )

                self._post(
                    "sheet_commit",
                    {
                        "spreadsheet_id": spreadsheet_id,
                        "stage_sheet_title": stage_title,
                        "target_sheet_title": sheet_name,
                        "expected_digest": digest,
                    },
                    idempotency_key=self._stable_key(
                        "sheet_commit", spreadsheet_id, sheet_name, rows_sha, digest
                    ),
                )
                committed = True
                inspected = self._post(
                    "sheet_inspect",
                    {
                        "spreadsheet_id": spreadsheet_id,
                        "sheet_title": sheet_name,
                        "date_column": self._date_column(sheet_name),
                    },
                )
                if inspected.get("found") is not True:
                    raise GoogleSheetsBridgeError(
                        f"committed sheet {sheet_name} is missing",
                        code="POST_COMMIT_MISSING",
                    )
                if int(inspected.get("row_count") or -1) != len(rows):
                    raise GoogleSheetsBridgeError(
                        f"post-commit row-count mismatch for {sheet_name}",
                        code="POST_COMMIT_ROW_COUNT_MISMATCH",
                    )
                if int(inspected.get("column_count") or -1) != len(rows[0]):
                    raise GoogleSheetsBridgeError(
                        f"post-commit column-count mismatch for {sheet_name}",
                        code="POST_COMMIT_COLUMN_COUNT_MISMATCH",
                    )
                if str(inspected.get("digest") or "").strip().lower() != digest:
                    raise GoogleSheetsBridgeError(
                        f"post-commit digest mismatch for {sheet_name}",
                        code="POST_COMMIT_DIGEST_MISMATCH",
                    )
                summaries[sheet_name] = {
                    "rows_total": len(rows),
                    "data_rows": max(0, len(rows) - 1),
                    "columns": len(rows[0]),
                    "sha256": digest,
                    "first_date": inspected.get("first_date"),
                    "last_date": inspected.get("last_date"),
                }
            except Exception:
                if not committed:
                    try:
                        self._post(
                            "sheet_abort",
                            {
                                "spreadsheet_id": spreadsheet_id,
                                "stage_sheet_title": stage_title,
                            },
                            idempotency_key=self._stable_key(
                                "sheet_abort", spreadsheet_id, sheet_name, rows_sha
                            ),
                        )
                    except Exception:
                        pass
                raise

        return {
            "spreadsheet_id": spreadsheet_id,
            "parity": True,
            "sheets": summaries,
        }

    def summary(self, *, spreadsheet_id: str) -> dict[str, Any]:
        spreadsheet_id = str(spreadsheet_id or "").strip()
        if not spreadsheet_id:
            raise ValueError("spreadsheet_id is required")
        summaries: dict[str, dict[str, Any]] = {}
        for sheet_name in ("D1", "SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS"):
            inspected = self._post(
                "sheet_inspect",
                {
                    "spreadsheet_id": spreadsheet_id,
                    "sheet_title": sheet_name,
                    "date_column": self._date_column(sheet_name),
                },
            )
            if inspected.get("found") is not True:
                continue
            rows_total = int(inspected.get("row_count") or 0)
            summaries[sheet_name] = {
                "rows_total": rows_total,
                "data_rows": max(0, rows_total - 1),
                "columns": int(inspected.get("column_count") or 0),
                "sha256": str(inspected.get("digest") or ""),
                "first_date": inspected.get("first_date"),
                "last_date": inspected.get("last_date"),
            }
        return {"spreadsheet_id": spreadsheet_id, "sheets": summaries}
