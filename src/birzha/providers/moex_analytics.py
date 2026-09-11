"""Real MOEX ALGOPACK TradeStats and FUTOI provider.

Paths and field semantics follow the official MOEX Algo client/documentation.
All outbound attempts pass through the mandatory BIRZHA request governor.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.market import Instrument
from birzha.upstream.moex import MOEX_AUTHENTICATED_POLICY, MOEX_ISS_PUBLIC_POLICY


ISS_BASE = "https://iss.moex.com/iss"
APIM_BASE = "https://apim.moex.com/iss"
FUTOI_SECURITY_CODES = {"GOLD": "GD"}


@dataclass(slots=True)
class AnalyticsResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class MoexAnalyticsError(RuntimeError):
    pass


class MoexAnalyticsClient:
    """MOEX analytical-data client with optional subscriber bearer token."""

    def __init__(
        self,
        *,
        control_plane: ProcessUpstreamControlPlane | None = None,
        bearer_token: str | None = None,
        opener=urlopen,
        timeout_seconds: float = 20.0,
        require_distributed_gate: bool = False,
    ) -> None:
        self._control_plane = control_plane or ProcessUpstreamControlPlane()
        self._token = bearer_token if bearer_token is not None else os.getenv("MOEX_ALGOPACK_BEARER_TOKEN")
        self._opener = opener
        self._timeout_seconds = timeout_seconds
        authenticated = bool(self._token)
        self._algopack_base = APIM_BASE if authenticated else ISS_BASE
        self._algopack_governor = self._control_plane.governor(
            "moex-algopack-auth" if authenticated else "moex-iss-public",
            MOEX_AUTHENTICATED_POLICY if authenticated else MOEX_ISS_PUBLIC_POLICY,
            require_distributed_gate=require_distributed_gate,
        )
        self._public_governor = self._control_plane.governor(
            "moex-iss-public",
            MOEX_ISS_PUBLIC_POLICY,
            require_distributed_gate=require_distributed_gate,
        )

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    @staticmethod
    def _table(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
        table = payload.get(name) or {}
        columns = table.get("columns") or []
        data = table.get("data") or []
        return [dict(zip(columns, row, strict=False)) for row in data]

    def _one_attempt(self, base: str, path: str, params: dict[str, object]) -> AnalyticsResponse:
        query = urlencode(params, doseq=True)
        url = f"{base}{path}"
        if query:
            url += f"?{query}"
        headers = {"Accept": "application/json", "User-Agent": "BIRZHA-MCP-FORECAST/0.1"}
        if self._token and base == APIM_BASE:
            headers["Authorization"] = f"Bearer {self._token}"
        req = Request(url, headers=headers)
        try:
            with self._opener(req, timeout=self._timeout_seconds) as response:
                return AnalyticsResponse(
                    status_code=int(getattr(response, "status", 200)),
                    headers={str(k): str(v) for k, v in response.headers.items()},
                    body=response.read(),
                )
        except HTTPError as exc:
            return AnalyticsResponse(
                status_code=int(exc.code),
                headers={str(k): str(v) for k, v in exc.headers.items()},
                body=exc.read(),
            )
        except URLError as exc:
            raise MoexAnalyticsError(f"MOEX analytics network error: {exc.reason}") from exc

    def _request(
        self,
        *,
        base: str,
        path: str,
        params: dict[str, object],
        authenticated_policy: bool,
    ) -> AnalyticsResponse:
        governor = self._algopack_governor if authenticated_policy else self._public_governor
        response = governor.execute(
            [(base, path, params)],
            lambda task: (lambda: self._one_attempt(task[0], task[1], task[2])),
        )[0]
        if response.status_code in {401, 403}:
            raise MoexAnalyticsError(
                "MOEX analytical data requires valid subscriber authorization for this dataset"
            )
        if response.status_code != 200:
            raise MoexAnalyticsError(f"MOEX analytics returned HTTP {response.status_code} for {path}")
        return response

    def _paged_rows(
        self,
        *,
        base: str,
        path: str,
        params: dict[str, object],
        table: str,
        authenticated_policy: bool,
        page_limit: int = 1000,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Read bounded analytical pages and fail closed if pagination stalls."""

        if max_pages <= 0:
            raise ValueError("max_pages must be > 0")
        rows: list[dict[str, Any]] = []
        start = 0
        previous_fingerprint: str | None = None
        for _ in range(max_pages):
            payload = self._request(
                base=base,
                path=path,
                params={**params, "start": start},
                authenticated_policy=authenticated_policy,
            ).json()
            page = self._table(payload, table)
            if not page:
                return rows

            fingerprint = json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if previous_fingerprint is not None and fingerprint == previous_fingerprint:
                raise MoexAnalyticsError(
                    f"MOEX analytics pagination stalled for {path}: repeated page at start={start}"
                )
            previous_fingerprint = fingerprint
            rows.extend(page)

            cursor_rows = self._table(payload, f"{table}.cursor")
            if cursor_rows:
                cursor = cursor_rows[0]
                total = int(cursor.get("TOTAL") or cursor.get("total") or len(rows))
                page_size = int(cursor.get("PAGESIZE") or cursor.get("pagesize") or len(page))
                if len(rows) >= total or page_size <= 0:
                    return rows
                start += page_size
                continue

            if len(page) < page_limit:
                return rows
            start += len(page)

        raise MoexAnalyticsError(
            f"MOEX analytics pagination exceeded safe max_pages={max_pages} for {path}"
        )

    def fetch_tradestats(
        self,
        instrument: Instrument,
        *,
        from_date: str,
        till_date: str,
        latest: bool = False,
    ) -> list[dict[str, Any]]:
        """Return raw five-minute TradeStats rows for a supported asset class."""

        market_code = _algopack_market_code(instrument)
        path = f"/datashop/algopack/{market_code}/tradestats/{instrument.secid}.json"
        params: dict[str, object] = {
            "iss.meta": "off",
            "from": from_date,
            "till": till_date,
            "limit": 1000,
        }
        if latest:
            params["latest"] = 1
        return self._paged_rows(
            base=self._algopack_base,
            path=path,
            params=params,
            table="data",
            authenticated_policy=self.authenticated,
        )

    def fetch_futoi(
        self,
        instrument: Instrument,
        *,
        from_date: str,
        till_date: str,
    ) -> list[dict[str, Any]]:
        """Return intraday open-interest rows split by client group for futures."""

        if instrument.asset_class != "future":
            raise MoexAnalyticsError("FUTOI is only applicable to futures instruments")
        security_code = _futoi_security_code(instrument)
        path = f"/analyticalproducts/futoi/securities/{security_code}.json"
        return self._paged_rows(
            base=ISS_BASE,
            path=path,
            params={
                "iss.meta": "off",
                "from": from_date,
                "till": till_date,
                "limit": 1000,
            },
            table="futoi",
            authenticated_policy=False,
        )


def _futoi_security_code(instrument: Instrument) -> str:
    root = (instrument.root_symbol or instrument.symbol).strip()
    if not root:
        raise ValueError("instrument root symbol is required for FUTOI")
    return FUTOI_SECURITY_CODES.get(root.upper(), root)


def _algopack_market_code(instrument: Instrument) -> str:
    if instrument.asset_class == "future":
        return "fo"
    if instrument.asset_class == "equity":
        return "eq"
    if instrument.asset_class == "fx":
        return "fx"
    raise MoexAnalyticsError(
        f"ALGOPACK TradeStats is not configured for asset_class={instrument.asset_class!r}"
    )
