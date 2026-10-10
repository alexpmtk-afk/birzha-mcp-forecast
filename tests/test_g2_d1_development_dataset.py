"""Deterministic six-market D1 Development extractor tests (in-memory only)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json

import pytest

from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore
from scripts.g2_d1_development_dataset import (
    MARKETS, _canonical, _root_key, _validate_schema, load_v2,
    make_dataset, run, EXTRACTOR_VERSION,
)
from birzha.application.warmup_session_evidence import warmup_d1_evidence_key


def _fixture_db():
    store = DuckDBHistoricalCandleStore()
    days = tuple((date(2021, 1, 1) + timedelta(days=i)).isoformat()
                 for i in range(21))
    for market in MARKETS:
        is_future = market in ("BR", "Si", "GOLD")
        is_index = market in ("IMOEX", "RTSI")
        secid = {
            "BR": "BRH1", "Si": "SiH1", "GOLD": "GDH1",
            "SBER": "SBER", "IMOEX": "IMOEX", "RTSI": "RTSI",
        }[market]
        asset = "future" if is_future else "index" if is_index else "equity"
        ins = Instrument(
            symbol=market, secid=secid, root_symbol=market if is_future else None,
            board="RFUD" if is_future else "SNDX" if is_index else "TQBR",
            engine="futures" if is_future else "stock",
            market="forts" if is_future else "index" if is_index else "shares",
            asset_class=asset,
            data_capabilities=("CANDLES",) if is_index
                else ("CANDLES", "VOLUME"),
        )
        store.record_sessions(_root_key(market), secid, days)
        store.mark_session_range_verified(_root_key(market), days[0], days[-1])
        candles = tuple(Candle(
            open=100.0+i, close=100.0+i, high=102.0+i, low=98.0+i,
            value=1000.0+i, volume=0.0 if is_index else 10.0+i,
            begin=f"{day}T10:00:00", end=f"{day}T23:49:59",
            completed=True, source="MOEX_ISS",
        ) for i, day in enumerate(days))
        store.upsert_series(CandleSeries(ins, "D1", candles))
    return store, days


def test_six_market_no_cross_contract_or_pretend_sma50_admission():
    store, days = _fixture_db()
    try:
        has_caps = _validate_schema(store._connection)
        selected, excluded, totals = make_dataset(
            store._connection, {}, has_caps=has_caps,
            from_day=days[0], till_day=days[-1],
        )
        assert len(selected) == 6
        assert len(excluded) == 120
        assert {r["market"] for r in selected} == set(MARKETS)
        assert all(x["session"] == days[-1] for x in selected)
        assert all(x["decision_knowledge_cutoff_at"] is None for x in selected)
        assert all(x["historical_first_receipt"] == "NOT_PROVEN" for x in selected)
        assert all(x["features"]["features"]["sma50"]["status"]
                   == "INSUFFICIENT_HISTORY" for x in selected)
        assert all(v["admitted_reconstructed"] == 1
                   and v["development_active_candidates"] == 21
                   for v in totals.values())
        assert all(x["calendar_evidence_origin"] == "ARCHIVED_ACTIVE_ROOT_CALENDAR"
                   for x in selected)
        imoex = next(x for x in selected if x["market"] == "IMOEX")
        assert imoex["features"]["features"]["volume_ratio20"]["status"] == "NOT_APPLICABLE"
    finally:
        store.close()


def test_extra_unclassified_session_is_excluded_not_compressed():
    store, days = _fixture_db()
    try:
        inst = store.stored_instrument("SBER")
        assert inst is not None
        # Extra bar on a non-recorded date between the 21 selected sessions:
        store.upsert_series(CandleSeries(inst, "D1", (
            Candle(open=99, close=99, high=100, low=98, value=1, volume=1,
                   begin="2021-01-05T01:00:00", end="2021-01-05T01:59:59"),
        )))
        data, excluded, totals = make_dataset(
            store._connection, {}, has_caps=True,
            from_day=days[0], till_day=days[-1],
        )
        assert not any(r["market"] == "SBER" for r in data)
        assert any(r["market"] == "SBER" and r["reason"] ==
                   "UNVERIFIED_EXTRA_OR_MISSING_D1_SESSION" for r in excluded)
        assert totals["SBER"]["admitted_reconstructed"] == 0
    finally:
        store.close()


def test_wrong_gold_equity_secid_fails_closed():
    store,days=_fixture_db()
    try:
        rows = store._connection.execute("""
            UPDATE historical_sessions SET secid='GOLD'
             WHERE symbol=? RETURNING trade_date
        """, [_root_key("GOLD")]).fetchall()
        assert len(rows) == 21
        data, excluded, totals = make_dataset(
            store._connection, {}, has_caps=True,
            from_day=days[0], till_day=days[-1],
        )
        assert totals["GOLD"]["admitted_reconstructed"] == 0
        assert all(r["reason"] == "WRONG_GOLD_CONTRACT"
                   for r in excluded if r["market"] == "GOLD")
    finally:
        store.close()


def test_do_not_select_reserved_holdout_dates_even_on_explicit_request():
    store,days=_fixture_db()
    try:
        with pytest.raises(ValueError,match="exposed Development"):
            make_dataset(store._connection,{},has_caps=True,
                         from_day="2023-01-01",till_day="2023-12-31")
    finally:
        store.close()


def test_output_byte_payload_and_feature_schema_deterministic():
    store,days=_fixture_db()
    try:
        first = make_dataset(store._connection,{},has_caps=True,
                             from_day=days[0],till_day=days[-1])
        second= make_dataset(store._connection,{},has_caps=True,
                             from_day=days[0],till_day=days[-1])
        assert first == second
        lines="".join(_canonical(x)+"\n" for x in first[0]).encode()
        assert hashlib.sha256(lines).hexdigest() == hashlib.sha256(
            "".join(_canonical(x)+"\n" for x in second[0]).encode()
        ).hexdigest()
        assert all(r["features"]["version"] if "version" in r["features"]
                   else r["features"]["schema"]
                   for r in first[0])
    finally:
        store.close()


def test_v2_checksum_and_contract_keys_required(tmp_path):
    archive_hash="c"*64
    days=tuple((date(2020,2,1)+timedelta(days=i)).isoformat() for i in range(20))
    manifest={}
    markets={}
    for market,secid in (("BR","BRH1"),("Si","SiH1"),("GOLD","GDH1")):
        record={
            "first_active_date":"2021-01-01",
            "origin":"RECONSTRUCTED_MOEX",
            "evidence_key":warmup_d1_evidence_key(
                secid,days,origin="RECONSTRUCTED_MOEX"),
            "expected_dates":list(days),
            "expected_count":20,
        }
        manifest[market]={secid:{k:record[k] for k in (
            "first_active_date","origin","evidence_key","expected_dates")}}
        markets[market]={"status":"RECONSTRUCTED_RESEARCH_ONLY",
                         "per_contract":{secid:record}}
    data={
        "report_schema_version":"D1_RECONSTRUCTED_WARMUP_AUDIT_V2",
        "source_sha256_before":archive_hash,
        "source_sha256_after":archive_hash,
        "calendar_origin":"RECONSTRUCTED_MOEX_CURRENT_QUERY",
        "original_unchanged":True,
        "strict_historical_as_known_at_T0":False,
        "evidence_manifest_sha256":hashlib.sha256(
            _canonical(manifest).encode()).hexdigest(),
        "markets":markets,
    }
    path=tmp_path/"report.json"
    path.write_text(_canonical(data))
    assert len(load_v2(path,archive_hash)) == 3
    with pytest.raises(ValueError,match="exact immutable archive"):
        load_v2(path,"d"*64)
    data["evidence_manifest_sha256"]="f"*64
    path.write_text(_canonical(data))
    with pytest.raises(ValueError,match="manifest SHA"):
        load_v2(path,archive_hash)
