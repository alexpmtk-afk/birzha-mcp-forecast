"""Authenticated Birzha client for the shared Google Apps Script Drive bridge.

The Yandex-hosted runtime reuses the already deployed owner-executed Apps Script
Web App used by Marketplaces. Birzha operations are namespaced (``birzha_*``),
so the existing Marketplace protocol remains backward compatible. The shared
secret is injected from Yandex Lockbox and never committed to Git.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx


class GoogleSheetsBridgeNotConfigured(RuntimeError):
    """The server-side Google Sheets mirror bridge is not configured."""


class GoogleSheetsBridgeError(RuntimeError):
    """The shared Google Apps Script bridge rejected or failed an operation."""


@dataclass(frozen=True, slots=True)
class GoogleSheetsBridgeConfig:
    bridge_url: str
    bridge_secret: str
    root_folder_id: str
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not self.bridge_url.startswith("https://script.google.com/macros/s/"):
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge URL is invalid")
        if not self.bridge_secret.strip():
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge secret is empty")
        if not self.root_folder_id.strip():
            raise GoogleSheetsBridgeNotConfigured("Google Sheets archive root id is empty")
        if self.timeout_seconds <= 0:
            raise GoogleSheetsBridgeNotConfigured("Google Sheets bridge timeout must be positive")


class GoogleSheetsBridge:
    """Fail-closed Birzha client for the shared Apps Script Web App."""

    _MAX_ATTEMPTS = 3
    _RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
    _ALLOWED_SHEETS = frozenset({"D1", "SESSIONS", "VERIFIED_RANGES", "SYNC_STATUS"})
    _ACTION_PREFIX = "birzha_"

    def __init__(self, config: GoogleSheetsBridgeConfig) -> None:
        self.config = config

    def _post(self, action: str, **payload: Any) -> dict[str, Any]:
        if not action.startswith(self._ACTION_PREFIX):
            raise ValueError("Birzha bridge actions must use the birzha_ namespace")
        body = {"secret": self.config.bridge_secret, "action": action, **payload}
        last_error: Exception | None = None
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=True) as client:
                    response = client.post(
                        self.config.bridge_url,
                        json=body,
                        headers={"Accept": "application/json"},
                    )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._MAX_ATTEMPTS:
                    time.sleep(0.5 * attempt)
                    continue
                raise GoogleSheetsBridgeError(
                    f"Google Sheets bridge request failed after {attempt} attempts: {exc}"
                ) from exc

            if not response.is_success:
                if response.status_code in self._RETRYABLE and attempt < self._MAX_ATTEMPTS:
                    time.sleep(0.5 * attempt)
                    continue
                raise GoogleSheetsBridgeError(
                    f"Google Sheets bridge HTTP {response.status_code}: {response.text[:500]}"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise GoogleSheetsBridgeError("Google Sheets bridge returned non-JSON") from exc
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise GoogleSheetsBridgeError(
                    f"Google Sheets bridge rejected {action}: {str(data)[:500]}"
                )
            return data
        raise GoogleSheetsBridgeError(f"Google Sheets bridge request failed: {last_error}")

    def health(self) -> dict[str, Any]:
        data = self._post("birzha_health")
        if str(data.get("root_id") or "") != self.config.root_folder_id:
            raise GoogleSheetsBridgeError(
                "Google Sheets bridge root mismatch: "
                f"{data.get('root_id')!r} != {self.config.root_folder_id!r}"
            )
        return data

    def ensure_archive(self, *, symbol: str) -> dict[str, Any]:
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("symbol is required")
        data = self._post("birzha_ensure_archive", symbol=symbol)
        spreadsheet_id = str(data.get("spreadsheet_id") or "").strip()
        folder_id = str(data.get("folder_id") or "").strip()
        if not spreadsheet_id or not folder_id:
            raise GoogleSheetsBridgeError(
                f"Google Sheets bridge returned incomplete archive target: {str(data)[:500]}"
            )
        return data

    @classmethod
    def _validated_sheets(
        cls, sheets: Mapping[str, Sequence[Sequence[Any]]]
    ) -> dict[str, list[list[Any]]]:
        if not sheets:
            raise ValueError("mirror snapshot must contain at least one sheet")
        result: dict[str, list[list[Any]]] = {}
        total_cells = 0
        for name, raw_rows in sheets.items():
            if name not in cls._ALLOWED_SHEETS:
                raise ValueError(f"unsupported mirror sheet: {name}")
            rows = [list(row) for row in raw_rows]
            if not rows:
                raise ValueError(f"mirror sheet {name} must contain a header row")
            width = len(rows[0])
            if width <= 0 or width > 40:
                raise ValueError(f"mirror sheet {name} has invalid width {width}")
            if len(rows) > 25000:
                raise ValueError(f"mirror sheet {name} exceeds the 25000 row safety limit")
            if any(len(row) != width for row in rows):
                raise ValueError(f"mirror sheet {name} is not rectangular")
            total_cells += len(rows) * width
            result[name] = rows
        if total_cells > 500000:
            raise ValueError("mirror snapshot exceeds the 500000 cell safety limit")
        return result

    def replace_snapshot(
        self,
        *,
        spreadsheet_id: str,
        sheets: Mapping[str, Sequence[Sequence[Any]]],
    ) -> dict[str, Any]:
        spreadsheet_id = spreadsheet_id.strip()
        if not spreadsheet_id:
            raise ValueError("spreadsheet_id is required")
        normalized = self._validated_sheets(sheets)
        data = self._post(
            "birzha_replace_snapshot",
            spreadsheet_id=spreadsheet_id,
            sheets=normalized,
        )
        if data.get("parity") is not True:
            raise GoogleSheetsBridgeError(
                f"Google Sheets bridge read-back parity failed: {str(data)[:500]}"
            )
        return data

    def summary(self, *, spreadsheet_id: str) -> dict[str, Any]:
        spreadsheet_id = spreadsheet_id.strip()
        if not spreadsheet_id:
            raise ValueError("spreadsheet_id is required")
        return self._post("birzha_summary", spreadsheet_id=spreadsheet_id)
