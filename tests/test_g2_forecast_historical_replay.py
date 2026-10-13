from dataclasses import replace
from datetime import datetime, timedelta
import pytest
import scripts.g2_forecast_historical_replay as module
from birzha.domain.market import Candle, CandleSeries, Instrument

SHA = "a" * 64
CUTOFF = "2025-10-10T23:59:59+03:00"

def fixture(symbol="SBER"):
    future = symbol in ("Si","BR","GOLD")
    index = symbol in ("IMOEX","RTSI")
    instrument = Instrument(symbol, symbol+"Z1" if future else symbol,
        "RFUD" if future else "SNDX" if index else "TQBR",
        "futures" if future else "stock", "forts" if future else "index" if index else "shares",
        "future" if future else "index" if index else "equity",
        data_capabilities=("CANDLES",) if index else ("CANDLES","VOLUME"))
    starts = {"D1":datetime.fromisoformat("2025-08-10T00:00:00+03:00"),
        "H1":datetime.fromisoformat("2025-10-07T00:00:00+03:00"),
        "M15":datetime.fromisoformat("2025-10-10T00:00:00+03:00")}
    steps={"D1":timedelta(days=1),"H1":timedelta(hours=1),"M15":timedelta(minutes=15)}
    series={}
    for tf in module.TIMEFRAMES:
        candles=[]
        for i in range(60):
            begin=starts[tf]+steps[tf]*i;end=begin+steps[tf]-timedelta(seconds=1)
            price=100+i
            candles.append(Candle(price,price+.5,price+1,price-1,None,100.,
                begin.isoformat(),end.isoformat(),True,available_at=end.isoformat(),
                available_at_confidence="EXACT"))
        series[tf]=CandleSeries(instrument,tf,tuple(candles))
    return instrument,series

@pytest.mark.parametrize("market",["SBER","Si","BR","GOLD","IMOEX","RTSI"])
def test_full_price_context_calls_original_engine(market,monkeypatch):
    instrument,series=fixture(market)
    original=module.build_forecast_from_snapshot
    calls=[]
    def measured(snapshot):
        calls.append(snapshot)
        return original(snapshot)
    monkeypatch.setattr(module,"build_forecast_from_snapshot",measured)
    report=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    assert report["assessment_status"]=="ENGINE_REPLAYED" and len(calls)==1
    payload=report["research_engine_result"]["engine_payload"]
    assert payload["engine_version"]==module.ENGINE_VERSION and payload["direction"]=="UP"
    assert len(payload["horizons"])==3 and report["journal_written"] is False
    assert report["historical_first_receipt"]=="NOT_PROVEN" and report["release_allowed"] is False

@pytest.mark.parametrize("tf",["D1","H1","M15"])
def test_missing_context_never_calls_forecast_or_invents_neutral(tf,monkeypatch):
    instrument,series=fixture()
    series[tf]=CandleSeries(instrument,tf,())
    def forbidden(snapshot):
        pytest.fail("missing data reached forecast")
    monkeypatch.setattr(module,"build_forecast_from_snapshot",forbidden)
    report=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    assert report["assessment_status"]=="MISSING_INPUTS"
    assert report["research_engine_result"] is None and report["input_counts"][tf]==0
    assert any(reason.startswith(tf+":") for reason in report["blockers"])

def test_future_candles_do_not_change_the_past_engine_result():
    instrument,series=fixture()
    before=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    candle=replace(series["M15"].candles[-1],begin="2025-10-11T00:00:00+03:00",
        end="2025-10-11T00:14:59+03:00",open=900.,close=999.,high=1000.,low=850.)
    series["M15"]=replace(series["M15"],candles=series["M15"].candles+(candle,))
    after=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    assert before==after

@pytest.mark.parametrize("bad",["2023-01-01T00:00:00Z","2024-03-01T00:00:00Z","2027-01-01T00:00:00Z"])
def test_reserved_period_is_refused_before_engine(bad):
    instrument,series=fixture()
    with pytest.raises(ValueError,match="2025-26"):
        module.replay_existing_forecast(instrument,cutoff_at=bad,series=series,source_sha256=SHA)

@pytest.mark.parametrize("kind",["boolean_price","wrong_secid","duplicate","partial_h1"])
def test_invalid_raw_context_is_refused(kind):
    instrument,series=fixture()
    if kind=="wrong_secid":
        series["H1"]=replace(series["H1"],instrument=replace(instrument,secid="OTHER"))
    elif kind=="duplicate":
        series["H1"]=replace(series["H1"],candles=series["H1"].candles+(series["H1"].candles[-1],))
    else:
        first=series["H1"].candles[0]
        changed=replace(first,close=True) if kind=="boolean_price" else replace(first,end="2025-10-07T00:29:59+03:00")
        series["H1"]=replace(series["H1"],candles=(changed,)+series["H1"].candles[1:])
    with pytest.raises(ValueError):
        module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)


def future_fixture(instrument):
    dates=tuple((datetime(2025,10,11)+timedelta(days=i)).date().isoformat() for i in range(20))
    candles=tuple(Candle(160+i,160.5+i,162+i,159+i,None,100.,
        day+"T00:00:00+03:00",day+"T23:59:59+03:00") for i,day in enumerate(dates))
    return CandleSeries(instrument,"D1",candles),dates

def test_original_engine_is_scored_at_all_three_horizons_without_mutating_replay():
    import json
    instrument,series=fixture()
    replay=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    before=json.dumps(replay,sort_keys=True)
    future,dates=future_fixture(instrument)
    result=module.evaluate_replay_outcomes(replay,future=future,expected_session_dates=dates)
    assert [r["horizon_sessions"] for r in result["outcomes"]]==[5,10,20]
    assert all(r["status"]=="OBSERVED" and r["direction_hit"] is True for r in result["outcomes"])
    assert all(r["absolute_move_error_percentage_points"] is not None for r in result["outcomes"])
    assert json.dumps(replay,sort_keys=True)==before and result["journal_written"] is False

def test_later_outcomes_remain_unknown_when_only_five_sessions_exist():
    instrument,series=fixture()
    replay=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    future,dates=future_fixture(instrument)
    future=replace(future,candles=future.candles[:5])
    rows=module.evaluate_replay_outcomes(replay,future=future,expected_session_dates=dates)["outcomes"]
    assert rows[0]["status"]=="OBSERVED"
    assert all(r["status"]=="PENDING" and r["actual_return_pct"] is None for r in rows[1:])

@pytest.mark.parametrize("defect",["roll","missing","partial","reserved"])
def test_future_gap_roll_partial_or_reserved_period_is_refused(defect):
    instrument,series=fixture()
    replay=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    future,dates=future_fixture(instrument)
    if defect=="roll":
        future=replace(future,instrument=replace(instrument,secid="NEXT"))
    elif defect=="missing":
        future=replace(future,candles=future.candles[1:])
    elif defect=="partial":
        future=replace(future,candles=(replace(future.candles[0],completed=False),)+future.candles[1:])
    else:
        dates=tuple((datetime(2023,1,1)+timedelta(days=i)).date().isoformat() for i in range(20))
    with pytest.raises(ValueError):
        module.evaluate_replay_outcomes(replay,future=future,expected_session_dates=dates)


def test_fake_calendar_dates_are_rejected_even_when_outcomes_are_pending():
    instrument,series=fixture()
    replay=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    future,dates=future_fixture(instrument)
    future=replace(future,candles=())
    invalid=tuple("2025-15-"+str(i).zfill(2) for i in range(1,21))
    with pytest.raises(ValueError):
        module.evaluate_replay_outcomes(replay,future=future,expected_session_dates=invalid)


def test_original_engine_result_cannot_change_between_prediction_and_assessment():
    instrument,series=fixture()
    replay=module.replay_existing_forecast(instrument,cutoff_at=CUTOFF,series=series,source_sha256=SHA)
    future,dates=future_fixture(instrument)
    replay["research_engine_result"]["engine_payload"]["horizons"][0]["expected_move_pct"] = 999.0
    with pytest.raises(ValueError,match="changed"):
        module.evaluate_replay_outcomes(replay,future=future,expected_session_dates=dates)


def test_overlapping_valid_duration_hourly_bars_are_refused():
    """Distinct increasing start times may still double count one price interval."""
    instrument, series = fixture()
    first, second = series["H1"].candles[:2]
    changed_begin = datetime.fromisoformat(first.begin) + timedelta(minutes=30)
    # The modified second bar remains exactly 1h (minus 1 second) long.
    overlapping = replace(
        second,
        begin=changed_begin.isoformat(),
        end=(changed_begin + timedelta(hours=1) - timedelta(seconds=1)).isoformat(),
    )
    series["H1"] = replace(
        series["H1"], candles=(first, overlapping) + series["H1"].candles[2:]
    )
    assert datetime.fromisoformat(overlapping.begin) > datetime.fromisoformat(first.begin)
    assert datetime.fromisoformat(overlapping.begin) < datetime.fromisoformat(first.end)
    with pytest.raises(ValueError, match="overlapping"):
        module.replay_existing_forecast(
            instrument, cutoff_at=CUTOFF, series=series, source_sha256=SHA
        )


def test_forty_eight_hour_d1_candle_is_not_one_completed_session():
    """An anomalous multi-day D1 must not satisfy the 50-bar input gate."""
    instrument, series = fixture()
    last = series["D1"].candles[-1]
    changed_end = datetime.fromisoformat(last.begin) + timedelta(days=2)
    assert changed_end < datetime.fromisoformat(CUTOFF)
    series["D1"] = replace(
        series["D1"],
        candles=series["D1"].candles[:-1] + (
            replace(last, end=changed_end.isoformat()),),
    )
    with pytest.raises(ValueError, match="D1 candle longer"):
        module.replay_existing_forecast(
            instrument, cutoff_at=CUTOFF, series=series, source_sha256=SHA
        )


@pytest.mark.parametrize("tf", ["H1", "M15"])
@pytest.mark.parametrize("bad_day", ["2024-12-31", "2027-01-01"])
def test_replay_refuses_old_or_later_intraday_bars_before_engine(tf, bad_day, monkeypatch):
    instrument, series = fixture()
    original = series[tf].candles[0]
    # One out-of-scope bar must reject the whole input, not get filtered.
    illegal = replace(original, begin=bad_day + "T00:00:00+03:00",
                      end=bad_day + ("T00:59:59+03:00" if tf == "H1" else "T00:14:59+03:00"))
    series[tf] = replace(series[tf], candles=(illegal,) + series[tf].candles[1:])
    monkeypatch.setattr(module, "build_forecast_from_snapshot",
                        lambda snapshot: pytest.fail("forbidden context reached Forecast V1"))
    with pytest.raises(ValueError, match="2025-01-01..2026-12-31"):
        module.replay_existing_forecast(
            instrument, cutoff_at=CUTOFF, series=series, source_sha256=SHA
        )
