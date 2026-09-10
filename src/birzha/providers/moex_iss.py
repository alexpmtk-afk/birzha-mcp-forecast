"""Real MOEX ISS provider behind the mandatory BIRZHA request governor."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.upstream.moex import MOEX_ISS_PUBLIC_POLICY


ISS_BASE = "https://iss.moex.com/iss"


@dataclass(slots=True)
class IssResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class MoexIssError(RuntimeError):
    pass


class MoexIssClient:
    """Small provider client using only official MOEX ISS HTTP endpoints.

    Every outbound call is executed through ``ProcessUpstreamControlPlane``.
    Provider code therefore cannot accidentally fan out faster than the global
    BIRZHA safety policy.
    """

    def __init__(
        self,
        *,
        control_plane: ProcessUpstreamControlPlane | None = None,
        opener=urlopen,
        timeout_seconds: float = 20.0,
        require_distributed_gate: bool = False,
    ) -> None:
        self._control_plane = control_plane or ProcessUpstreamControlPlane()
        self._opener = opener
        self._timeout_seconds = timeout_seconds
        self._governor = self._control_plane.governor(
            "moex-iss-public",
            MOEX_ISS_PUBLIC_POLICY,
            require_distributed_gate=require_distributed_gate,
        )

    @staticmethod
    def _table(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
        table = payload.get(name) or {}
        columns = table.get("columns") or []
        data = table.get("data") or []
        return [dict(zip(columns, row, strict=False)) for row in data]

    def _request(self, path: str, params: dict[str, object] | None = None) -> IssResponse:
        query = urlencode(params or {}, doseq=True)
        url = f"{ISS_BASE}{path}"
        if query:
            url += f"?{query}"

        def one_attempt() -> IssResponse:
            req = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "BIRZHA-MCP-FORECAST/0.1",
                },
            )
            try:
                with self._opener(req, timeout=self._timeout_seconds) as response:
                    return IssResponse(
                        status_code=int(getattr(response, "status", 200)),
                        headers={str(k): str(v) for k, v in response.headers.items()},
                        body=response.read(),
                    )
            except HTTPError as exc:
                return IssResponse(
                    status_code=int(exc.code),
                    headers={str(k): str(v) for k, v in exc.headers.items()},
                    body=exc.read(),
                )
            except (URLError, TimeoutError, ConnectionError) as exc:
                return IssResponse(
                    status_code=503,
                    headers={"X-BIRZHA-TRANSIENT": type(exc).__name__},
                    body=b"",
                )

        response = self._governor.execute([url], lambda _: one_attempt)[0]
        if response.status_code != 200:
            raise MoexIssError(f"MOEX ISS returned HTTP {response.status_code} for {path}")
        return response

    def _request_many(self, requests: Iterable[tuple[str, dict[str, object]]]) -> list[IssResponse]:
        materialized = list(requests)

        def build(task: tuple[str, dict[str, object]]):
            path, params = task
            query = urlencode(params, doseq=True)
            url = f"{ISS_BASE}{path}?{query}" if query else f"{ISS_BASE}{path}"

            def one_attempt() -> IssResponse:
                req = Request(url, headers={"Accept": "application/json", "User-Agent": "BIRZHA-MCP-FORECAST/0.1"})
                try:
                    with self._opener(req, timeout=self._timeout_seconds) as response:
                        return IssResponse(
                            status_code=int(getattr(response, "status", 200)),
                            headers={str(k): str(v) for k, v in response.headers.items()},
                            body=response.read(),
                        )
                except HTTPError as exc:
                    return IssResponse(
                        status_code=int(exc.code),
                        headers={str(k): str(v) for k, v in exc.headers.items()},
                        body=exc.read(),
                    )
                except (URLError, TimeoutError, ConnectionError) as exc:
                    return IssResponse(
                        status_code=503,
                        headers={"X-BIRZHA-TRANSIENT": type(exc).__name__},
                        body=b"",
                    )

            return one_attempt

        responses = self._governor.execute(materialized, build)
        for response in responses:
            if response.status_code != 200:
                raise MoexIssError(f"MOEX ISS returned HTTP {response.status_code}")
        return responses

    def resolve_active_future(self, root_symbol: str, *, as_of: date | None = None) -> Instrument:
        """Resolve the most liquid non-expired MOEX futures contract for a root symbol."""

        root = root_symbol.strip()
        if not root:
            raise ValueError("root_symbol must be non-empty")
        day = as_of or datetime.now(timezone.utc).date()

        payload = self._request(
            "/engines/futures/markets/forts/boards/RFUD/securities.json",
            {
                "iss.meta": "off",
                "iss.only": "securities,marketdata",
                "securities.columns": "SECID,SHORTNAME,NAME,BOARDID,LASTTRADEDATE,ASSETCODE",
                "marketdata.columns": "SECID,LAST,VALTODAY,OPENPOSITION,NUMTRADES",
            },
        ).json()
        securities = self._table(payload, "securities")
        market = {row.get("SECID"): row for row in self._table(payload, "marketdata")}

        candidates: list[tuple[float, float, float, str, dict[str, Any]]] = []
        root_lower = root.lower()
        for row in securities:
            secid = str(row.get("SECID") or "")
            asset = str(row.get("ASSETCODE") or "")
            if not (asset.lower() == root_lower or secid.lower().startswith(root_lower)):
                continue
            ltd_raw = row.get("LASTTRADEDATE")
            if not ltd_raw:
                continue
            try:
                ltd = date.fromisoformat(str(ltd_raw)[:10])
            except ValueError:
                continue
            if ltd < day:
                continue
            md = market.get(secid) or {}
            turnover = float(md.get("VALTODAY") or 0.0)
            oi = float(md.get("OPENPOSITION") or 0.0)
            trades = float(md.get("NUMTRADES") or 0.0)
            candidates.append((turnover, oi, trades, secid, row))

        if not candidates:
            raise MoexIssError(f"No active MOEX futures contract found for {root_symbol!r}")

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
        _, _, _, secid, row = candidates[0]
        return Instrument(
            symbol=root,
            secid=secid,
            board=str(row.get("BOARDID") or "RFUD"),
            engine="futures",
            market="forts",
            asset_class="future",
            name=str(row.get("NAME") or row.get("SHORTNAME") or secid),
            root_symbol=root,
            last_trade_date=str(row.get("LASTTRADEDATE") or "")[:10] or None,
        )

    @staticmethod
    def _interval_for_timeframe(timeframe: str) -> int:
        mapping = {"M1": 1, "M10": 10, "H1": 60, "D1": 24, "W1": 7, "MN1": 31}
        try:
            return mapping[timeframe.upper()]
        except KeyError as exc:
            raise ValueError(f"unsupported native MOEX timeframe: {timeframe}") from exc

    def _fetch_native_candles(
        self,
        instrument: Instrument,
        *,
        interval: int,
        from_date: str,
        till_date: str,
    ) -> tuple[Candle, ...]:
        path = (
            f"/engines/{instrument.engine}/markets/{instrument.market}/boards/"
            f"{instrument.board}/securities/{instrument.secid}/candles.json"
        )
        base_params: dict[str, object] = {
            "iss.meta": "off",
            "from": from_date,
            "till": till_date,
            "interval": interval,
            "candles.columns": "open,close,high,low,value,volume,begin,end",
        }
        first = self._request(path, {**base_params, "start": 0}).json()
        rows = self._table(first, "candles")
        cursor_rows = self._table(first, "candles.cursor")
        if cursor_rows:
            cursor = cursor_rows[0]
            total = int(cursor.get("TOTAL") or len(rows))
            page_size = int(cursor.get("PAGESIZE") or max(1, len(rows)))
        else:
            total = len(rows)
            page_size = max(1, len(rows))

        if cursor_rows:
            starts = list(range(page_size, total, page_size))
            if starts:
                responses = self._request_many((path, {**base_params, "start": start}) for start in starts)
                for response in responses:
                    rows.extend(self._table(response.json(), "candles"))
        elif rows:
            # The candles endpoint commonly omits a cursor and caps a page at 500 rows.
            # Continue until a short/empty page. Fail closed if MOEX repeats a page.
            seen_pages = {_page_signature(rows)}
            start = len(rows)
            for _ in range(1000):
                page_payload = self._request(path, {**base_params, "start": start}).json()
                page = self._table(page_payload, "candles")
                if not page:
                    break
                signature = _page_signature(page)
                if signature in seen_pages:
                    raise MoexIssError(f"MOEX ISS repeated candle page for {instrument.secid} start={start}")
                seen_pages.add(signature)
                rows.extend(page)
                if len(page) < page_size:
                    break
                start += len(page)
            else:
                raise MoexIssError(f"MOEX ISS candle pagination exceeded safety limit for {instrument.secid}")

        candles: list[Candle] = []
        for row in rows:
            begin = str(row.get("begin") or "")
            end = str(row.get("end") or "")
            if not begin or not end:
                continue
            candles.append(
                Candle(
                    open=_float_or_none(row.get("open")),
                    close=_float_or_none(row.get("close")),
                    high=_float_or_none(row.get("high")),
                    low=_float_or_none(row.get("low")),
                    value=_float_or_none(row.get("value")),
                    volume=_float_or_none(row.get("volume")),
                    begin=begin,
                    end=end,
                    completed=True,
                )
            )
        candles.sort(key=lambda candle: candle.begin)
        return tuple(candles)

    @staticmethod
    def _aggregate_m15(candles: tuple[Candle, ...]) -> tuple[Candle, ...]:
        buckets: dict[str, list[Candle]] = {}
        for candle in candles:
            try:
                dt = datetime.fromisoformat(candle.begin.replace("Z", "+00:00"))
            except ValueError:
                continue
            minute = (dt.minute // 15) * 15
            bucket_dt = dt.replace(minute=minute, second=0, microsecond=0)
            key = bucket_dt.isoformat()
            buckets.setdefault(key, []).append(candle)

        result: list[Candle] = []
        for key in sorted(buckets):
            bucket_dt = datetime.fromisoformat(key)
            group = sorted(buckets[key], key=lambda candle: candle.begin)
            highs = [c.high for c in group if c.high is not None]
            lows = [c.low for c in group if c.low is not None]
            values = [c.value for c in group if c.value is not None]
            volumes = [c.volume for c in group if c.volume is not None]
            result.append(
                Candle(
                    open=group[0].open,
                    close=group[-1].close,
                    high=max(highs) if highs else None,
                    low=min(lows) if lows else None,
                    value=sum(values) if values else None,
                    volume=sum(volumes) if volumes else None,
                    begin=bucket_dt.isoformat(sep=" "),
                    end=(bucket_dt + timedelta(minutes=15) - timedelta(seconds=1)).isoformat(sep=" "),
                    completed=all(c.completed for c in group),
                )
            )
        return tuple(result)

    def fetch_candles(
        self,
        instrument: Instrument,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
        completed_only: bool = True,
    ) -> CandleSeries:
        tf = timeframe.upper()
        if tf == "M15":
            candles = self._aggregate_m15(
                self._fetch_native_candles(
                    instrument,
                    interval=1,
                    from_date=from_date,
                    till_date=till_date,
                )
            )
        else:
            candles = self._fetch_native_candles(
                instrument,
                interval=self._interval_for_timeframe(tf),
                from_date=from_date,
                till_date=till_date,
            )
        if completed_only:
            candles = tuple(c for c in candles if c.completed)
        return CandleSeries(instrument=instrument, timeframe=tf, candles=candles)



def _page_signature(rows: list[dict[str, Any]]) -> tuple[int, str, str]:
    if not rows:
        return (0, "", "")
    first = str(rows[0].get("begin") or rows[0])
    last = str(rows[-1].get("begin") or rows[-1])
    return (len(rows), first, last)

def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
