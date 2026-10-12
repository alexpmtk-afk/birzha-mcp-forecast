"""Q120..123: actual old-date mechanics, no LIVE and no protected OOS."""
from dataclasses import replace
from datetime import date,timedelta
from hashlib import sha256
import json

import pytest

from birzha.domain.market import Candle,CandleSeries
from scripts.g2_d1_development_dataset import (
    FUTURES,MARKETS,_root_key,_active_calendar,_canonical,_validate_schema,make_dataset,
)
from scripts.g2_d1_historical_diagnostic import (
    HORIZONS,METHOD,_complete_close,evaluate_historical_development,run,
)
from test_g2_d1_development_dataset import _fixture_db


def history_45(store):
    start=date(2021,1,1)
    extra=tuple((start+timedelta(days=i)).isoformat() for i in range(21,46))
    for market in MARKETS:
        secid={"BR":"BRH1","Si":"SiH1","GOLD":"GDH1",
               "SBER":"SBER","IMOEX":"IMOEX","RTSI":"RTSI"}[market]
        instrument=store.stored_instrument(secid)
        assert instrument
        store.record_sessions(_root_key(market),secid,extra)
        store.mark_session_range_verified(_root_key(market),extra[0],extra[-1])
        bars=tuple(Candle(
            open=100.0+i,close=100.0+i,high=102.0+i,low=98.0+i,
            value=1000.0+i,volume=0 if market in ("IMOEX","RTSI") else 20+i,
            begin=f"{day}T10:00:00",end=f"{day}T23:49:59",
            completed=True,source="MOEX_ISS",
        ) for i,day in enumerate(extra,start=21))
        store.upsert_series(CandleSeries(instrument,"D1",bars))
    return extra


def extracted(store):
    return make_dataset(store._connection,{},has_caps=_validate_schema(store._connection),
                        from_day="2021-01-01",till_day="2021-02-15")[0]


def test_q120_historical_2021_development_evaluates_exact_5_10_20_later_sessions():
    store,_=_fixture_db()
    try:
        history_45(store)
        rows=extracted(store)
        result,excluded,reasons=evaluate_historical_development(
            store._connection,rows,till_day="2021-02-15")
        assert len(rows)>0
        assert set(x["market"] for x in result)==set(MARKETS)
        assert set(x["horizon"] for x in result)==set(HORIZONS)
        assert all(x["baseline"]==METHOD and x["Forecast_V1_measured"] is False
                   and x["historical_first_receipt"]=="NOT_PROVEN"
                   and x["strict_historical_as_known_at_T0"] is False
                   for x in result)
        assert all(x["reference_close"]>0 and x["target_close"]>0 for x in result)
        assert all(x["session"]<x["future_sessions"][0]
                   and x["future_sessions"][-1]==x["target_session"]
                   and len(x["future_sessions"])==x["horizon"]
                   for x in result)
        assert len(result)+len(excluded)==len(rows)*len(HORIZONS)
        assert reasons["FUTURE_OUTSIDE_DEVELOPMENT"]>0
    finally:
        store.close()


def test_q121_future_contract_roll_never_leaks_previous_root_future_bars():
    store,_=_fixture_db()
    try:
        dates=history_45(store)
        first=dates[4]
        new_secid="BRM1"
        store._connection.execute(
            "UPDATE historical_sessions SET secid=? WHERE symbol=? AND trade_date>=?",
            [new_secid,_root_key("BR"),first])
        rows=extracted(store)
        # decision dates before a roll still use their own original
        # features, but forward validation must refuse a different SECID.
        outcome,excluded,_=evaluate_historical_development(
            store._connection,rows,till_day="2021-02-15")
        assert any(e["market"]=="BR"
                   and e["reason"]=="CONTRACT_ROLL_INSIDE_FUTURE_WINDOW"
                   for e in excluded)
        assert not any(x["market"]=="BR" and x["session"]<first
                       and x["target_session"]>=first for x in outcome)
    finally:
        store.close()


def test_q122_modifying_future_close_changes_outcome_but_not_past_features():
    store,_=_fixture_db()
    try:
        days=history_45(store)
        before=extracted(store)
        t0="2021-01-21"
        target=next(r for r in before if r["market"]=="SBER" and r["session"]==t0)
        baseline,_excluded,_=evaluate_historical_development(
            store._connection,[target],till_day="2021-02-15")
        future_day="2021-01-26"
        # Modify a future bar only; this is synthetic test data.
        store._connection.execute("""
            UPDATE historical_candles
            SET close=200.0, high=202.0
            WHERE timeframe='D1' AND secid='SBER' AND begin LIKE ?
        """,[future_day+"%"])
        after=extracted(store)
        unchanged=next(r for r in after if r["market"]=="SBER" and r["session"]==t0)
        newer,_excluded,_=evaluate_historical_development(
            store._connection,[unchanged],till_day="2021-02-15")
        assert target["features"]==unchanged["features"]
        assert next(x for x in baseline if x["horizon"]==5)["target_close"]!=(
               next(x for x in newer if x["horizon"]==5)["target_close"])
    finally:
        store.close()


def test_q123_reject_reserved_2023_24_and_nonreproducible_historical_evidence():
    store,_=_fixture_db()
    try:
        history_45(store)
        rows=extracted(store)
        for day in ("2023-01-01","2024-12-31","2026-10-12"):
            with pytest.raises(ValueError,match="2021-22"):
                evaluate_historical_development(store._connection,rows,till_day=day)
        bad=dict(rows[0], historical_first_receipt="SIGNED_FIRST_RECEIPT")
        with pytest.raises(ValueError,match="reconstructed"):
            evaluate_historical_development(store._connection,[bad],till_day="2021-02-15")
        with pytest.raises(ValueError,match="duplicate"):
            evaluate_historical_development(store._connection,[rows[0],rows[0]],
                                            till_day="2021-02-15")
    finally:
        store.close()


def test_offline_real_file_fixture_run_is_deterministic_and_archive_sha_unchanged(tmp_path):
    from birzha.storage.historical_store import DuckDBHistoricalCandleStore
    archive=tmp_path/"archived_2021.duckdb"
    store=DuckDBHistoricalCandleStore(str(archive))
    # reuse fixture's synthetic data by ATTACH read-only source? Instead
    # construct a source under the real on-disk path deterministically.
    from birzha.domain.market import Instrument
    days=tuple((date(2021,1,1)+timedelta(days=i)).isoformat() for i in range(46))
    for market in MARKETS:
        is_future=market in FUTURES
        secid={"SBER":"SBER","Si":"SiH1","BR":"BRH1","GOLD":"GDH1",
               "IMOEX":"IMOEX","RTSI":"RTSI"}[market]
        is_index=market in ("IMOEX","RTSI")
        instrument=Instrument(symbol=market,secid=secid,
            root_symbol=market if is_future else None,
            engine="futures" if is_future else "stock",
            market="forts" if is_future else "index" if is_index else "shares",
            board="RFUD" if is_future else "SNDX" if is_index else "TQBR",
            asset_class="future" if is_future else "index" if is_index else "equity",
            data_capabilities=("CANDLES",) if is_index else ("CANDLES","VOLUME"))
        store.record_sessions(_root_key(market),secid,days)
        store.mark_session_range_verified(_root_key(market),days[0],days[-1])
        store.upsert_series(CandleSeries(instrument,"D1",tuple(
            Candle(open=100.+i,close=100.+i,high=102.+i,low=98.+i,
                   value=1000.+i,volume=0 if is_index else 10.+i,
                   begin=f"{day}T10:00:00",end=f"{day}T23:49:59",
                   completed=True,source="MOEX_ISS")
            for i,day in enumerate(days))))
    store.close()
    before=sha256(archive.read_bytes()).hexdigest()
    manifest={m:{} for m in FUTURES}
    evidence={"report_schema_version":"D1_RECONSTRUCTED_WARMUP_AUDIT_V2",
        "source_sha256_before":before,"source_sha256_after":before,
        "strict_historical_as_known_at_T0":False,"original_unchanged":True,
        "calendar_origin":"RECONSTRUCTED_MOEX_CURRENT_QUERY",
        "markets":{m:{"status":"RECONSTRUCTED_RESEARCH_ONLY","per_contract":{}}
                   for m in FUTURES},
        "evidence_manifest_sha256":sha256(_canonical(manifest).encode()).hexdigest()}
    witness=tmp_path/"warmup_v2.json"
    witness.write_text(_canonical(evidence),encoding="utf-8")
    outputs=tmp_path/"out"
    a=run(archive,witness,outputs)
    b=run(archive,witness,outputs)
    assert a==b
    assert a["evaluated_horizon_pairs"]>0
    assert a["protected_2023_2024_holdout_opened"] is False
    assert a["Forecast_V1_measured"] is False
    assert sha256(archive.read_bytes()).hexdigest()==before
    assert len((outputs/"g2_d1_historical_results.jsonl").read_text().splitlines())==a[
        "evaluated_horizon_pairs"]
    assert len((outputs/"g2_d1_historical_exclusions.jsonl").read_text().splitlines())==a[
        "excluded_horizon_pairs"]
    # A directory at the last output target must fail BEFORE any
    # partial report is written.
    conflict = tmp_path/"collision_out"
    conflict.mkdir()
    (conflict/"g2_d1_historical_manifest.json").mkdir()
    with pytest.raises(FileExistsError, match="unlike historical"):
        run(archive, witness, conflict)
    assert not (conflict/"g2_d1_historical_results.jsonl").exists()
    assert not (conflict/"g2_d1_historical_exclusions.jsonl").exists()
    p=outputs/"g2_d1_historical_results.jsonl"
    p.write_text("FORGED\n")
    with pytest.raises(FileExistsError,match="unlike historical"):
        run(archive,witness,outputs)


@pytest.mark.parametrize("bad_end", (
    "2021-01-26T09:00:00",   # earlier than begin
    "2021-01-26T10:00:00",   # zero duration
    "2021-01-27T23:49:59",   # spans longer than one day
))
def test_q124_invalid_future_d1_end_time_is_excluded_not_scored(bad_end):
    store, _ = _fixture_db()
    try:
        history_45(store)
        t0 = next(r for r in extracted(store)
                  if r["market"] == "SBER" and r["session"] == "2021-01-21")
        store._connection.execute("""
            UPDATE historical_candles SET end_time=?
            WHERE timeframe='D1' AND secid='SBER' AND begin LIKE ?
        """, [bad_end, "2021-01-26%"])
        assert _complete_close(store._connection, "SBER", "2021-01-26") is None
        scored, rejected, reasons = evaluate_historical_development(
            store._connection, [t0], till_day="2021-02-15")
        assert not any(r["horizon"] == 5 for r in scored)
        assert any(r["horizon"] == 5 and
                   r["reason"] == "MISSING_OR_INVALID_FUTURE_D1"
                   for r in rejected)
        assert reasons["MISSING_OR_INVALID_FUTURE_D1"] >= 1
    finally:
        store.close()


def test_q125_development_calendar_query_is_sql_bounded_before_oos():
    store, _ = _fixture_db()
    try:
        history_45(store)
        store.record_sessions(_root_key("SBER"), "SBER", ("2023-01-05",))
        store.mark_session_range_verified(_root_key("SBER"),
                                          "2023-01-05", "2023-01-05")
        class RecordedConnection:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.calls = []

            def execute(self, sql, args=None):
                self.calls.append((sql, args))
                return self.wrapped.execute(sql, args or [])

        proxy = RecordedConnection(store._connection)
        calendar = _active_calendar(proxy, "SBER", through_day="2021-02-15")
        assert calendar and all("2021-01-01" <= day <= "2021-02-15"
                                for day, _ in calendar)
        assert "2023-01-05" not in [day for day, _ in calendar]
        sessions_query = next((sql, params) for sql, params in proxy.calls
                              if "FROM historical_sessions" in sql)
        assert "trade_date>=?" in sessions_query[0]
        assert "trade_date<=?" in sessions_query[0]
        assert sessions_query[1] == [_root_key("SBER"), "2021-01-01", "2021-02-15"]
        ranges_query = next((sql, params) for sql, params in proxy.calls
                            if "FROM historical_session_verified_ranges" in sql)
        assert "till_date>=?" in ranges_query[0]
        assert "from_date<=?" in ranges_query[0]
        with pytest.raises(ValueError, match="2021-22"):
            _active_calendar(proxy, "SBER", through_day="2023-01-05")
    finally:
        store.close()
