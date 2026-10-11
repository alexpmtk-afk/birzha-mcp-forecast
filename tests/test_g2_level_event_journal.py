"""Q-085 original-observation research pilot; no external trades/forecast."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import pytest
from birzha.application.g2_level_event_journal import (
    LevelEventPilot, Refused, Collision,
)
from birzha.domain.market import Candle, Instrument

T0=datetime(2026,10,11,10,0,tzinfo=timezone.utc)

def inst():
    return Instrument(symbol="SBER",secid="SBER",board="TQBR",
                      engine="stock",market="shares",asset_class="equity")

def bar(i, *, low=100.,high=110.,close=104.,source="TEST_MOCK"):
    begin=T0+timedelta(minutes=15*i)
    end=begin+timedelta(minutes=15)
    return Candle(open=104.,high=high,low=low,close=close,
                  volume=100.,value=10400.,
                  begin=begin.isoformat(),end=end.isoformat(),
                  available_at=end.isoformat(),
                  observed_at=(end+timedelta(seconds=10)).isoformat(),
                  completed=True,available_at_confidence="EXACT",source=source)

class Clock:
    now=T0
    def __call__(self): return self.now

def put(j,clock,i,b=None,raw=None):
    b = b or bar(i)
    clock.now=datetime.fromisoformat(b.observed_at)+timedelta(seconds=2)
    return j.append(inst(),b,original_source_bytes=raw or ("RAW-%d"%i).encode(),
                    atr_price_scale=2.)

def test_first_observation_then_persisted_detection_and_restart(tmp_path):
    p=tmp_path/"events.sqlite3"; clock=Clock()
    with LevelEventPilot(p,clock=clock) as j:
        for i in range(19):
            x=put(j,clock,i)
            assert x["history_ready"] is False and x["new_events"]==0
        x=put(j,clock,19)
        assert x["history_ready"] is True and x["new_events"]==2
        ev=j.events()
        assert {e["role"] for e in ev}=={"SUPPORT","RESISTANCE"}
        assert all(e["kind"]=="LEVEL_FIRST_DETECTED" for e in ev)
        assert all(not e["external_source_authenticated"] for e in ev)
        initial=j.verify()
        retry=put(j,clock,19)
        assert retry["status"]=="DUPLICATE_IDENTICAL" and retry["new_events"]==0
    with LevelEventPilot(p,clock=clock) as j:
        assert j.verify()==initial
        put(j,clock,20)
        assert j.verify()["receipt_count"]==21

def test_retest_and_new_extreme_not_fabricated_role_flip(tmp_path):
    clock=Clock()
    with LevelEventPilot(tmp_path/"a.sqlite3",clock=clock) as j:
        for i in range(20): put(j,clock,i)
        put(j,clock,20,bar(20,low=100.1,high=109.,close=101.))
        assert any(e["kind"]=="LEVEL_RETEST_HELD" for e in j.events())
        put(j,clock,21,bar(21,low=99.,high=109.,close=101.))
        assert any(e["kind"]=="LEVEL_PRICE_UPDATED" and e["role"]=="SUPPORT"
                   and e["price"]==99 for e in j.events())
        assert all(e["role_flip_verified"] is False for e in j.events())

def test_immutable_collision_and_late_backfill_rejected(tmp_path):
    clock=Clock()
    with LevelEventPilot(tmp_path/"a.sqlite3",clock=clock) as j:
        put(j,clock,0)
        old=j.verify()
        with pytest.raises(Collision): put(j,clock,0,raw=b"ALTERED")
        assert j.verify()==old
        put(j,clock,2)
        with pytest.raises(Refused): put(j,clock,1)
        assert j.verify()["receipt_count"]==2

def test_gaps_need_fresh_twenty_contiguous_candles(tmp_path):
    clock=Clock()
    with LevelEventPilot(tmp_path/"a.sqlite3",clock=clock) as j:
        for i in range(19): put(j,clock,i)
        assert not put(j,clock,21)["history_ready"]
        for i in range(22,40): put(j,clock,i)
        assert j.verify()["event_count"]==0
        assert put(j,clock,40)["new_events"]==2

@pytest.mark.parametrize("case",["partial","unknown","future","stale",
                                  "naive","badohlc","nan","wrong_tf","source"])
def test_refuses_untrusted_or_future_bars(tmp_path,case):
    clock=Clock()
    with LevelEventPilot(tmp_path/"a.sqlite3",clock=clock) as j:
        b=bar(0)
        clock.now=datetime.fromisoformat(b.observed_at)+timedelta(seconds=2)
        if case=="partial": b=replace(b,completed=False)
        elif case=="unknown": b=replace(b,available_at_confidence="UNKNOWN")
        elif case=="future": b=replace(b,observed_at=(clock.now+timedelta(seconds=5)).isoformat())
        elif case=="stale": clock.now+=timedelta(minutes=16)
        elif case=="naive": b=replace(b,observed_at="2026-10-11T10:15:10")
        elif case=="badohlc": b=replace(b,low=200.)
        elif case=="nan": b=replace(b,volume=float("nan"))
        elif case=="wrong_tf": b=replace(b,end=(T0+timedelta(minutes=30)).isoformat())
        elif case=="source": b=replace(b,source=None)
        with pytest.raises(Refused):
            j.append(inst(),b,original_source_bytes=b"raw",atr_price_scale=2.)
        assert j.verify()["receipt_count"]==0

def test_append_only_and_chain_detect_external_corruption(tmp_path):
    p=tmp_path/"a.sqlite3"; clock=Clock()
    with LevelEventPilot(p,clock=clock) as j:
        put(j,clock,0)
        with pytest.raises(sqlite3.DatabaseError):
            j.db.execute("UPDATE level_receipts SET raw_sha='hacked' WHERE id=1")
        with pytest.raises(sqlite3.DatabaseError):
            j.db.execute("DELETE FROM level_receipts")
    db=sqlite3.connect(str(p))
    db.execute("DROP TRIGGER level_receipts_no_update")
    db.execute("UPDATE level_receipts SET raw_sha='hacked' WHERE id=1")
    db.commit(); db.close()
    with pytest.raises(Collision): LevelEventPilot(p,clock=clock)

def test_cannot_overwrite_unrelated_database(tmp_path):
    p=tmp_path/"other.sqlite3"
    d=sqlite3.connect(str(p))
    d.execute("CREATE TABLE unrelated(x INTEGER)");d.close()
    with pytest.raises(Refused): LevelEventPilot(p)
    with pytest.raises(Refused): LevelEventPilot(":memory:")

def test_stale_exact_retry_is_read_only_after_restart(tmp_path):
    path=tmp_path/"a.sqlite3"
    clock=Clock()
    with LevelEventPilot(path,clock=clock) as j:
        fresh=put(j,clock,0)
        assert fresh["status"]=="NEW_RECEIPT"
    clock.now+=timedelta(days=1)
    with LevelEventPilot(path,clock=clock) as j:
        same=j.append(inst(),bar(0),original_source_bytes=b"RAW-0",
                      atr_price_scale=2.)
        assert same["status"]=="DUPLICATE_IDENTICAL"
        assert same["first_received_at"]==fresh["first_received_at"]
        assert j.verify()["receipt_count"]==1
        with pytest.raises(Collision):
            j.append(inst(),bar(0),original_source_bytes=b"DIFFERENT",
                     atr_price_scale=2.)
        with pytest.raises(Refused):
            j.append(inst(),bar(1),original_source_bytes=b"NEW_LATE",
                     atr_price_scale=2.)
        assert j.verify()["receipt_count"]==1


def test_old_levels_are_not_retested_across_m15_gap(tmp_path):
    clock=Clock()
    with LevelEventPilot(tmp_path/"segments.sqlite3",clock=clock) as j:
        for i in range(20):
            put(j,clock,i)
        assert j.verify()["event_count"]==2
        skipped=put(j,clock,21)
        assert not skipped["history_ready"]
        assert skipped["new_events"]==2
        assert {e["kind"] for e in j.events()[-2:]}=={"LEVEL_HISTORY_GAP"}
        for i in range(22,40):
            nxt=put(j,clock,i)
            assert not nxt["history_ready"]
        last=put(j,clock,40)
        assert last["history_ready"] and last["new_events"]==2
        assert {e["kind"] for e in j.events()[-2:]}=={"LEVEL_FIRST_DETECTED"}
        assert all(e["role_flip_verified"] is False for e in j.events())



@pytest.mark.parametrize("column,value", [
    ("bar_start","2035-01-01T00:00:00Z"),
    ("bar_end","2035-01-01T00:15:00Z"),
    ("observed","2035-01-01T00:16:00Z"),
    ("stream",'["WRONG","ID","BOARD","ENGINE","MARKET"]'),
])
def test_corrupted_receipt_index_fields_rejected_on_reopen(tmp_path,column,value):
    path=tmp_path/"integrity.sqlite3"; clock=Clock()
    with LevelEventPilot(path,clock=clock) as j:
        put(j,clock,0)
    db=sqlite3.connect(path)
    db.execute("DROP TRIGGER level_receipts_no_update")
    db.execute("UPDATE level_receipts SET "+column+"=? WHERE id=1",(value,))
    db.commit(); db.close()
    # The read-only opener must refuse missing immutable trigger. A replayed
    # trigger does not conceal the independent metadata mismatch.
    with pytest.raises(Refused): LevelEventPilot(path,clock=clock)
    db=sqlite3.connect(path)
    db.execute("""CREATE TRIGGER level_receipts_no_update
      BEFORE UPDATE ON level_receipts
      BEGIN SELECT RAISE(ABORT,'append only receipts'); END""")
    db.commit(); db.close()
    with pytest.raises(Collision): LevelEventPilot(path,clock=clock)


@pytest.mark.parametrize("field,new_value",[
    ("receipt_id",2),("stream","OTHER"),("role","RESISTANCE"),
    ("kind","LEVEL_RETEST_HELD"),("price",999.),
])
def test_corrupted_event_link_and_indexes_rejected(tmp_path,field,new_value):
    path=tmp_path/"integrity.sqlite3"; clock=Clock()
    with LevelEventPilot(path,clock=clock) as j:
        for i in range(20): put(j,clock,i)
        assert j.verify()["event_count"]==2
    db=sqlite3.connect(path)
    db.execute("DROP TRIGGER level_events_no_update")
    db.execute("UPDATE level_events SET "+field+"=? WHERE id=1",(new_value,))
    db.commit()
    db.execute("""CREATE TRIGGER level_events_no_update
      BEFORE UPDATE ON level_events
      BEGIN SELECT RAISE(ABORT,'append only events'); END""")
    db.commit();db.close()
    with pytest.raises(Collision): LevelEventPilot(path,clock=clock)


def test_removed_immutable_trigger_refused_even_when_all_rows_hash_clean(tmp_path):
    path=tmp_path/"integrity.sqlite3"; clock=Clock()
    with LevelEventPilot(path,clock=clock) as j: put(j,clock,0)
    db=sqlite3.connect(path)
    db.execute("DROP TRIGGER level_receipts_no_delete");db.commit();db.close()
    with pytest.raises(Refused): LevelEventPilot(path,clock=clock)


def test_missing_receipt_table_refused_without_rebuilding(tmp_path):
    path=tmp_path/"integrity.sqlite3";clock=Clock()
    with LevelEventPilot(path,clock=clock) as j: put(j,clock,0)
    db=sqlite3.connect(path)
    db.execute("DROP TRIGGER level_receipts_no_delete")
    db.execute("DROP TRIGGER level_receipts_no_update")
    db.execute("DROP TABLE level_receipts")
    db.commit();db.close()
    with pytest.raises(Refused): LevelEventPilot(path,clock=clock)
