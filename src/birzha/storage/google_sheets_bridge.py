"""Synchronous Google Drive Bridge v1 client for the BIRZHA market mirror.

Business/archive semantics stay in Birzha. This module implements only the
versioned transport/security contract defined by the shared Google Drive Bridge
v1 standard.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

import httpx


class GoogleSheetsBridgeNotConfigured(RuntimeError):
    """The server-side Google Sheets mirror bridge is not configured."""


class GoogleSheetsBridgeError(RuntimeError):
    """Bridge v1 rejected or failed an operation."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = bool(retryable)


@dataclass(frozen=True, slots=True)
class GoogleSheetsBridgeConfig:
    bridge_url: str
    bridge_secret: str
    root_folder_id: str
    project_id: str = "birzha"
    timeout_seconds: float = 120.0
    max_attempts: int = 3
    chunk_rows: int = 500

    def __post_init__(self) -> None:
        if not self.bridge_url.startswith("https://script.google.com/macros/s/"):
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge URL is invalid")
        if not self.bridge_secret.strip():
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge secret is empty")
        if self.project_id != "birzha":
            raise GoogleSheetsBridgeNotConfigured("BIRZHA Bridge v1 project_id must be 'birzha'")
        if not self.root_folder_id.strip():
            raise GoogleSheetsBridgeNotConfigured("Google Sheets archive root id is empty")
        if self.timeout_seconds <= 0:
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge timeout must be positive")
        if self.max_attempts < 1 or self.max_attempts > 6:
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge max_attempts must be 1..6")
        if self.chunk_rows < 1 or self.chunk_rows > 1000:
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge chunk_rows must be 1..1000")


class GoogleSheetsBridge:
    """Fail-closed synchronous client for a dedicated Birzha Bridge v1 deployment."""

    _RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
    _MUTATIONS = frozenset(
        {
            "sheet_ensure",
            "sheet_stage_begin",
            "sheet_write_chunk",
            "sheet_commit",
            "sheet_abort",
        }
    )

    def __init__(self, config: GoogleSheetsBridgeConfig) -> None:
        self.config = config

    @staticmethod
    def new_request_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def new_idempotency_key(prefix: str = "mutation") -> str:
        return f"{prefix}:{uuid.uuid4()}"

    def _post(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        action = action.strip().lower()
        if not action:
            raise ValueError("bridge action is required")
        if action in self._MUTATIONS and not str(idempotency_key or "").strip():
            raise ValueError(f"idempotency_key is required for mutating action {action}")

        req_id = str(request_id or self.new_request_id()).strip()
        if not req_id:
            raise ValueError("request_id is required")
        body: dict[str, Any] = {
            "secret": self.config.bridge_secret,
            "project_id": self.config.project_id,
            "request_id": req_id,
            "action": action,
            "payload": payload or {},
        }
        if idempotency_key:
            body["idempotency_key"] = str(idempotency_key)

        # Retries replay this exact logical envelope, including request/idempotency IDs.
        last_error: BaseException | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                with httpx.Client(
                    timeout=self.config.timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    response = client.post(
                        self.config.bridge_url,
                        json=body,
                        headers={"Accept": "application/json"},
                    )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.config.max_attempts:
                    self._backoff(attempt)
                    continue
                raise GoogleSheetsBridgeError(
                    "TRANSPORT_ERROR", type(exc).__name__, retryable=True
                ) from exc

            if response.status_code in self._RETRYABLE_HTTP and attempt < self.config.max_attempts:
                self._backoff(attempt)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise GoogleSheetsBridgeError(
                    "HTTP_ERROR", f"bridge HTTP {response.status_code}", retryable=False
                ) from exc

            try:
                data = response.json()
            except ValueError as exc:
                raise GoogleSheetsBridgeError(
                    "NON_JSON_RESPONSE", "bridge returned non-JSON", retryable=False
                ) from exc
            if not isinstance(data, dict):
                raise GoogleSheetsBridgeError(
                    "INVALID_RESPONSE", "bridge response is not an object", retryable=False
                )
            if str(data.get("request_id") or "") != req_id:
                raise GoogleSheetsBridgeError(
                    "REQUEST_ID_MISMATCH", "bridge echoed a different request_id", retryable=False
                )
            if int(data.get("protocol_version") or 0) != 1:
                raise GoogleSheetsBridgeError(
                    "PROTOCOL_MISMATCH", "expected protocol_version=1", retryable=False
                )
            if str(data.get("project_id") or "") != self.config.project_id:
                raise GoogleSheetsBridgeError(
                    "PROJECT_MISMATCH", "bridge responded for a different project", retryable=False
                )

            if data.get("ok") is not True:
                err = data.get("error") if isinstance(data.get("error"), dict) else {}
                bridge_error = GoogleSheetsBridgeError(
                    str(err.get("code") or "BRIDGE_REJECTED"),
                    str(err.get("message") or "bridge rejected request"),
                    retryable=bool(err.get("retryable")),
                )
                if bridge_error.retryable and attempt < self.config.max_attempts:
                    self._backoff(attempt)
                    continue
                raise bridge_error

            result = data.get("result")
            if not isinstance(result, dict):
                raise GoogleSheetsBridgeError(
                    "INVALID_RESPONSE", "bridge result is not an object", retryable=False
                )
            return dict(result)

        raise GoogleSheetsBridgeError(
            "TRANSPORT_ERROR",
            type(last_error).__name__ if last_error else "unknown",
            retryable=True,
        )

    @staticmethod
    def _backoff(attempt: int) -> None:
        base = min(8.0, 0.75 * (2 ** (attempt - 1)))
        time.sleep(base + random.uniform(0.0, base * 0.25))

    def health(self) -> dict[str, Any]:
        result = self._post("health")
        if str(result.get("project_id") or "") != self.config.project_id:
            raise GoogleSheetsBridgeError("PROJECT_MISMATCH", "deep health project mismatch")
        if int(result.get("protocol_version") or 0) != 1:
            raise GoogleSheetsBridgeError("PROTOCOL_MISMATCH", "deep health protocol mismatch")
        if str(result.get("root_id") or "") != self.config.root_folder_id:
            raise GoogleSheetsBridgeError("ROOT_MISMATCH", "deep health root mismatch")
        caps = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
        required = {
            "google_sheets_chunked": True,
            "fixed_root_file_id_guard": True,
            "idempotent_mutations": True,
            "global_script_lock": False,
        }
        for key, expected in required.items():
            if caps.get(key) is not expected:
                raise GoogleSheetsBridgeError(
                    "CAPABILITY_MISMATCH", f"deep health capability {key} != {expected!r}"
                )
        return result

    def sheet_ensure(
        self,
        *,
        path: str = "",
        filename: str = "",
        spreadsheet_id: str = "",
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._post(
            "sheet_ensure",
            {"path": path, "filename": filename, "spreadsheet_id": spreadsheet_id},
            idempotency_key=idempotency_key,
        )

    def sheet_stage_begin(
        self,
        *,
        spreadsheet_id: str,
        sheet_title: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._post(
            "sheet_stage_begin",
            {"spreadsheet_id": spreadsheet_id, "sheet_title": sheet_title},
            idempotency_key=idempotency_key,
        )

    def sheet_write_chunk(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        start_row: int,
        start_col: int,
        values: Sequence[Sequence[Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = [list(row) for row in values]
        if not normalized or not normalized[0]:
            raise ValueError("sheet chunk must be a non-empty 2D array")
        width = len(normalized[0])
        if any(len(row) != width for row in normalized):
            raise ValueError("sheet chunk must be rectangular")
        if len(normalized) > self.config.chunk_rows:
            raise ValueError("sheet chunk exceeds configured chunk_rows")
        raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        chunk_sha = hashlib.sha256(raw).hexdigest()
        return self._post(
            "sheet_write_chunk",
            {
                "spreadsheet_id": spreadsheet_id,
                "stage_sheet_title": stage_sheet_title,
                "start_row": int(start_row),
                "start_col": int(start_col),
                "values": normalized,
                "chunk_sha256": chunk_sha,
            },
            idempotency_key=idempotency_key,
        )

    def sheet_verify(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        date_column: int = 0,
    ) -> dict[str, Any]:
        return self._post(
            "sheet_verify",
            {
                "spreadsheet_id": spreadsheet_id,
                "stage_sheet_title": stage_sheet_title,
                "date_column": int(date_column),
            },
        )

    def sheet_commit(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        target_sheet_title: str,
        expected_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._post(
            "sheet_commit",
            {
                "spreadsheet_id": spreadsheet_id,
                "stage_sheet_title": stage_sheet_title,
                "target_sheet_title": target_sheet_title,
                "expected_digest": expected_digest,
            },
            idempotency_key=idempotency_key,
        )

    def sheet_abort(
        self,
        *,
        spreadsheet_id: str,
        stage_sheet_title: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._post(
            "sheet_abort",
            {"spreadsheet_id": spreadsheet_id, "stage_sheet_title": stage_sheet_title},
            idempotency_key=idempotency_key,
        )
