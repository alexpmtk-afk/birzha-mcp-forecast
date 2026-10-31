"""Historical MOEX futures contract resolution for causal research/backfills."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from birzha.providers.continuous_futures import (
    ContinuousFuturesEvidenceError,
    select_completed_session_volume,
    _OUTRIGHT,
)

from birzha.domain.market import Instrument
from birzha.providers.moex_iss import MoexIssClient, MoexIssError
from birzha.providers.moex_instrument_contract import (
    moex_calendar_id,
    moex_data_capabilities,
    moex_roll_policy,
    moex_session_profile,
)


class MoexHistoricalFutureResolver:
    """One T-1 volume-based decision policy for historical futures roots.

    ISS historical rows are reconstructed after the fact; their first
    availability timestamp is NOT attested. This resolver must not be used to
    claim strict as-of-source publication without a separate evidence gate.
    """

    def __init__(self, client: MoexIssClient) -> None:
        self._client = client
        self._day_cache: dict[tuple[str, date], list[dict[str, Any]]] = {}
        self._decision_cache: dict[tuple[str, date], Instrument] = {}
        self._expiry_cache: dict[str, str] = {}

    def _official_expiry(self, secid: str) -> str:
        """Look up documented exact-contract last trade date; never infer."""
        if secid in self._expiry_cache:
            return self._expiry_cache[secid]
        requests = (
            (f"/engines/futures/markets/forts/boards/rfud/securities/{secid}.json",
             {"iss.meta": "off", "iss.only": "securities"}, "securities"),
            (f"/securities/{secid}.json",
             {"iss.meta": "off", "iss.only": "description"}, "description"),
        )
        for path, params, table_name in requests:
            try:
                response = self._client._request(path, params)  # noqa: SLF001
                rows = self._client._table(response.json(), table_name)  # noqa: SLF001
            except (MoexIssError, KeyError, ValueError):
                continue
            for row in rows:
                if table_name == "securities":
                    if _text(row, "SECID").casefold() != secid.casefold():
                        continue
                    raw = _text(row, "LASTTRADEDATE")
                else:
                    if _text(row, "name").upper() not in ("LSTTRADE", "LASTTRADEDATE"):
                        continue
                    raw = _text(row, "value")
                try:
                    expiry = date.fromisoformat(raw[:10]).isoformat()
                except ValueError:
                    continue
                self._expiry_cache[secid] = expiry
                return expiry
        raise MoexIssError(f"No authoritative LASTTRADEDATE for {secid}; refusing rollover")

    def _with_expiries(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            if not _OUTRIGHT.fullmatch(_text(row, "SECID")):
                result.append(row)
                continue
            if _text(row, "LASTTRADEDATE"):
                result.append(row)
                continue
            amended = dict(row)
            amended["LASTTRADEDATE"] = self._official_expiry(_text(row, "SECID"))
            result.append(amended)
        return result

    def _day_rows(self, root: str, day: date) -> list[dict[str, Any]]:
        key = (root.casefold(), day)
        if key not in self._day_cache:
            response = self._client._request(  # noqa: SLF001
                "/history/engines/futures/markets/forts/securities.json",
                {
                    "iss.meta": "off",
                    "iss.only": "history",
                    "history.columns": (
                        "TRADEDATE,SECID,BOARDID,ASSETCODE,VALUE,VOLUME,"
                        "OPENPOSITIONVALUE,OPENPOSITION,SHORTNAME,CLOSE,"
                        "LASTTRADEDATE,LASTDELDATE,MINSTEP,STEPPRICE,LOTVOLUME,CURRENCYID"
                    ),
                    "date": day.isoformat(),
                    "assetcode": root,
                },
            )
            payload = response.json()
            source = self._client._table(payload, "history")  # noqa: SLF001
            self._day_cache[key] = self._with_expiries([
                row for row in source
                if _text(row, "TRADEDATE")[:10] == day.isoformat()
                and _text(row, "ASSETCODE").casefold() == root.casefold()
            ])
        return self._day_cache[key]

    def _previous_observed_session(self, root: str, target: date) -> tuple[date, list[dict[str, Any]]]:
        """Only previous dates; neither same-day VALUE nor future volume used."""
        for lag in range(1, 22):
            prior = target - timedelta(days=lag)
            rows = self._day_rows(root, prior)
            if rows:
                return prior, rows
        raise MoexIssError(
            f"No completed prior ISS trade-session evidence within 21 days for {root} {target}"
        )

    def resolve(self, root_symbol: str, as_of: date) -> Instrument:
        root = root_symbol.strip()
        if not root:
            raise ValueError("root_symbol must be non-empty")
        key = (root.casefold(), as_of)
        if key not in self._decision_cache:
            prior, rows = self._previous_observed_session(root, as_of)
            try:
                decision = select_completed_session_volume(
                    root,
                    decision_session=prior,
                    effective_session=as_of,
                    decision_rows=rows,
                )
            except ContinuousFuturesEvidenceError as exc:
                raise MoexIssError(
                    f"Historical futures T-1 selection blocked: {root} {as_of}: {exc}"
                ) from exc
            matching = [r for r in rows if _text(r, "SECID") == decision.secid]
            if len(matching) != 1:
                raise MoexIssError("Selected contract must have one unique source row")
            instrument = _pick_instrument(root, as_of, matching)
            self._decision_cache[key] = Instrument(
                **{
                    **instrument.to_dict(),
                    "source": "MOEX_ISS_HISTORY_T_MINUS_1_RECONSTRUCTED",
                    "roll_policy": decision.roll_policy,
                }
            )
        return self._decision_cache[key]

    def timeline(
        self, root_symbol: str, from_date: date, till_date: date
    ) -> tuple[tuple[date, Instrument], ...]:
        """Retrospective timeline on *observed exchange TRADEDATE* dates only.

        Do not remove weekends blindly. MOEX extra-session mapping to official
        trade day MUST be verified externally; no calendar-day false attribution.
        """
        root = root_symbol.strip()
        if not root:
            raise ValueError("root_symbol must be non-empty")
        if from_date > till_date:
            raise ValueError("from_date must not be after till_date")
        rows = []
        day = from_date
        while day <= till_date:
            if self._day_rows(root, day):
                rows.append((day, self.resolve(root, day)))
            day += timedelta(days=1)
        return tuple(rows)


def _pick_instrument(root: str, as_of: date, rows: list[dict[str, Any]]) -> Instrument:
    root_lower = root.lower()
    candidates: list[tuple[float, float, float, str, dict[str, Any]]] = []
    for row in rows:
        secid = _text(row, "SECID")
        asset = _text(row, "ASSETCODE")
        if not secid:
            continue
        if asset and asset.lower() != root_lower:
            continue
        if not asset and not secid.lower().startswith(root_lower):
            continue
        value = _number(row, "VALUE") or 0.0
        oi_value = _number(row, "OPENPOSITIONVALUE") or _number(row, "OPENPOSITION") or 0.0
        volume = _number(row, "VOLUME") or 0.0
        candidates.append((value, oi_value, volume, secid, row))
    if not candidates:
        raise MoexIssError(f"No historical MOEX futures contract found for {root!r} on {as_of.isoformat()}")
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    _, _, _, secid, row = candidates[0]
    return Instrument(
        symbol=root,
        secid=secid,
        board=_text(row, "BOARDID") or "RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        name=_text(row, "SHORTNAME") or secid,
        root_symbol=root,
        last_trade_date=_text(row, "LASTTRADEDATE")[:10] or None,
        expiration_date=_text(row, "LASTTRADEDATE")[:10] or None,
        settlement_date=_text(row, "LASTDELDATE")[:10] or None,
        currency=_text(row, "CURRENCYID") or None,
        tick_size=_number(row, "MINSTEP"),
        tick_value=_number(row, "STEPPRICE"),
        contract_multiplier=_number(row, "LOTVOLUME"),
        calendar_id=moex_calendar_id(
            engine="futures",
            market="forts",
            board=_text(row, "BOARDID") or "RFUD",
        ),
        session_profile=moex_session_profile("future"),
        data_capabilities=moex_data_capabilities("future"),
        roll_policy=moex_roll_policy("future"),
        source="MOEX_ISS_HISTORY",
    )


def _first(row: dict[str, Any], key: str) -> object | None:
    for candidate in (key, key.lower(), key.upper()):
        if candidate in row and row[candidate] is not None:
            return row[candidate]
    return None


def _text(row: dict[str, Any], key: str) -> str:
    value = _first(row, key)
    return str(value).strip() if value is not None else ""


def _number(row: dict[str, Any], key: str) -> float | None:
    value = _first(row, key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(row: dict[str, Any], key: str) -> int | None:
    value = _first(row, key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
