from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import scripts.g2_historical_engine_batch as batch
from test_g2_forecast_historical_replay import fixture, future_fixture, CUTOFF


def store(path, value):
    raw = json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def bundles(markets=batch.MARKETS):
    inputs, futures = [], []
    for market in markets:
        instrument, series = fixture(market)
        future, dates = future_fixture(instrument)
        inputs.append({"case_id": market, "instrument": instrument.to_dict(),
                       "cutoff_at": CUTOFF, "series": {tf: [c.to_dict() for c in s.candles] for tf, s in series.items()}})
        futures.append({"case_id": market, "instrument": instrument.to_dict(),
                        "expected_session_dates": list(dates), "candles": [c.to_dict() for c in future.candles]})
    return ({"schema": batch.INPUT_SCHEMA, "scope": batch.SCOPE, "cases": inputs},
            {"schema": batch.FUTURE_SCHEMA, "scope": batch.SCOPE, "cases": futures})


def freeze(tmp_path, inputs):
    source, out = tmp_path/"inputs.json", tmp_path/"predictions.json"
    sha = store(source, inputs)
    result, prediction_sha = batch.freeze_batch(source, bundle_sha256=sha, output=out)
    return source, out, result, prediction_sha


def assess(tmp_path, out, sha, futures, name="results.json"):
    future_path = tmp_path/"future.json"
    future_sha = store(future_path, futures)
    return batch.assess_batch(out, predictions_sha256=sha, future_path=future_path,
                             future_sha256=future_sha, output=tmp_path/name)


def test_six_markets_two_stage_engine_and_same_row_baselines(tmp_path):
    inputs, futures = bundles()
    source, out, frozen, sha = freeze(tmp_path, inputs)
    before = out.read_bytes(), source.read_bytes()
    result, result_sha = assess(tmp_path, out, sha, futures)
    assert len(frozen["predictions"]) == 6
    assert all(p["replay"]["assessment_status"] == "ENGINE_REPLAYED" for p in frozen["predictions"])
    assert len(result["rows"]) == 6 and len(result["summary"]) == 18
    for row in result["summary"]:
        assert row["observed"] == row["directional"] == row["numeric_pairs"] == 1
        assert row["direction_hits"] == row["always_up_hits"] == 1
        assert row["direction_accuracy_pct"] == row["always_up_accuracy_pct"] == 100
        assert row["mean_absolute_move_error_pp"] is not None
    assert before == (out.read_bytes(), source.read_bytes())
    assert hashlib.sha256((tmp_path/"results.json").read_bytes()).hexdigest() == result_sha
    assert result["release_allowed"] is result["journal_written"] is result["independent_quality_proven"] is False
    assert "исходов 1" in batch.russian_summary(result)


def test_only_five_future_sessions_leave_later_metrics_null(tmp_path):
    inputs, futures = bundles(("SBER",))
    futures["cases"][0]["candles"] = futures["cases"][0]["candles"][:5]
    _, out, _, sha = freeze(tmp_path, inputs)
    result, _ = assess(tmp_path, out, sha, futures)
    rows = result["summary"][:3]
    assert rows[0]["observed"] == 1
    for row in rows[1:]:
        assert row["pending"] == 1 and row["direction_accuracy_pct"] is None
        assert row["direction_hits"] is row["mean_absolute_move_error_pp"] is None


def test_missing_inputs_and_absent_future_never_become_zero_accuracy(tmp_path):
    inputs, futures = bundles(("SBER", "Si"))
    inputs["cases"][0]["series"]["H1"] = []
    futures["cases"] = []
    _, out, _, sha = freeze(tmp_path, inputs)
    result, _ = assess(tmp_path, out, sha, futures)
    assert [r["assessment_status"] for r in result["rows"]] == ["MISSING_INPUTS", "FUTURE_NOT_SUPPLIED"]
    assert all(r["direction_accuracy_pct"] is None for r in result["summary"])
    assert result["summary"][0]["input_refused"] == 1
    assert result["summary"][0]["pending"] == 0
    assert result["summary"][3]["pending"] == 1
    assert "точность не определена" in batch.russian_summary(result)


def test_neutral_and_directional_cases_use_distinct_denominators(tmp_path, monkeypatch):
    inputs, futures = bundles(("SBER",))
    original = batch.replay_existing_forecast
    def neutral(*args, **kwargs):
        replay = original(*args, **kwargs)
        for horizon in replay["research_engine_result"]["engine_payload"]["horizons"]:
            horizon["direction"] = "NEUTRAL"
        replay["replay_sha256"] = batch._report_sha(replay)
        return replay
    monkeypatch.setattr(batch, "replay_existing_forecast", neutral)
    _, out, _, sha = freeze(tmp_path, inputs)
    result, _ = assess(tmp_path, out, sha, futures)
    assert all(r["neutral_observed"] == 1 and r["directional"] == 0 and r["direction_accuracy_pct"] is None for r in result["summary"][:3])


def test_repeat_runs_are_identical_and_existing_output_is_untouched(tmp_path):
    inputs, _ = bundles(("SBER",))
    source, out, _, sha = freeze(tmp_path, inputs)
    _, other_sha = batch.freeze_batch(source, bundle_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), output=tmp_path/"second.json")
    assert sha == other_sha
    with pytest.raises(ValueError, match="already exists"):
        batch.freeze_batch(source, bundle_sha256="a"*64, output=out)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == sha


@pytest.mark.parametrize("defect", ["future_in_freeze", "duplicate_id", "duplicate_origin", "reserved", "missing_completed", "boolean_price", "overlap", "multiday", "wrong_scope"])
def test_bad_inputs_produce_no_output(tmp_path, defect):
    inputs, _ = bundles(("SBER",))
    case = inputs["cases"][0]
    if defect == "future_in_freeze":
        case["future"] = []
    elif defect in ("duplicate_id", "duplicate_origin"):
        duplicate = deepcopy(case)
        if defect == "duplicate_origin":
            duplicate["case_id"] = "another"
        inputs["cases"].append(duplicate)
    elif defect == "reserved":
        case["series"]["D1"][0]["begin"] = "2023-01-01T00:00:00+03:00"
    elif defect == "missing_completed":
        del case["series"]["D1"][0]["completed"]
    elif defect == "boolean_price":
        case["series"]["D1"][0]["close"] = True
    elif defect == "overlap":
        case["series"]["H1"][1]["begin"] = "2025-10-07T00:30:00+03:00"
        case["series"]["H1"][1]["end"] = "2025-10-07T01:29:59+03:00"
    elif defect == "multiday":
        case["series"]["D1"][0]["end"] = "2025-08-12T00:00:00+03:00"
    else:
        inputs["scope"] = "STRICT_PIT"
    source = tmp_path/"inputs.json"
    sha = store(source, inputs)
    with pytest.raises(ValueError):
        batch.freeze_batch(source, bundle_sha256=sha, output=tmp_path/"bad.json")
    assert not (tmp_path/"bad.json").exists()


def test_sha_and_duplicate_json_keys_fail_closed(tmp_path):
    source = tmp_path/"input.json"
    source.write_text('{"schema":"one","schema":"two"}')
    for sha in ("a"*64, hashlib.sha256(source.read_bytes()).hexdigest()):
        with pytest.raises(ValueError):
            batch.freeze_batch(source, bundle_sha256=sha, output=tmp_path/"bad.json")
    assert not (tmp_path/"bad.json").exists()


@pytest.mark.parametrize("defect", ["roll", "gap", "unknown_id", "reserved_dates", "bad_calendar", "partial"])
def test_future_bundle_integrity_before_any_result_write(tmp_path, defect):
    inputs, futures = bundles(("SBER",))
    _, out, _, sha = freeze(tmp_path, inputs)
    case = futures["cases"][0]
    if defect == "roll":
        case["instrument"]["secid"] = "OTHER"
    elif defect == "gap":
        case["candles"] = case["candles"][1:]
    elif defect == "unknown_id":
        case["case_id"] = "unknown"
    elif defect == "reserved_dates":
        case["expected_session_dates"][0] = "2023-01-01"
    elif defect == "bad_calendar":
        case["expected_session_dates"][0] = "2025-15-01"
    else:
        case["candles"][0]["completed"] = False
    with pytest.raises(ValueError):
        assess(tmp_path, out, sha, futures)
    assert not (tmp_path/"results.json").exists()


def test_modified_frozen_replay_is_rejected_even_with_new_file_hash(tmp_path):
    inputs, futures = bundles(("SBER",))
    _, out, frozen, _ = freeze(tmp_path, inputs)
    frozen["predictions"][0]["replay"]["research_engine_result"]["engine_payload"]["reference_price"] = 999
    changed_sha = store(out, frozen)
    with pytest.raises(ValueError, match="sealed"):
        assess(tmp_path, out, changed_sha, futures)


def test_atomic_late_collision_does_not_replace_file(tmp_path, monkeypatch):
    inputs, _ = bundles(("SBER",))
    source = tmp_path/"input.json"
    sha = store(source, inputs)
    output = tmp_path/"predictions.json"
    original = batch.os.link
    def collide(src, dst):
        Path(dst).write_bytes(b"already frozen")
        return original(src, dst)
    monkeypatch.setattr(batch.os, "link", collide)
    with pytest.raises(FileExistsError):
        batch.freeze_batch(source, bundle_sha256=sha, output=output)
    assert output.read_bytes() == b"already frozen"
    assert not list(tmp_path.glob(".g2-batch-*"))


def test_actual_two_commands_run_and_keep_prediction_file(tmp_path):
    inputs, futures = bundles(("SBER",))
    source, future = tmp_path/"input.json", tmp_path/"future.json"
    input_sha, future_sha = store(source, inputs), store(future, futures)
    output = tmp_path/"predictions.json"
    command = [sys.executable, "-m", "scripts.g2_historical_engine_batch"]
    first = subprocess.run(command+["freeze", "--bundle", str(source), "--bundle-sha256", input_sha, "--output", str(output)], capture_output=True, text=True, encoding="utf-8")
    assert first.returncode == 0, first.stderr
    frozen_bytes = output.read_bytes()
    second = subprocess.run(command+["assess", "--predictions", str(output), "--predictions-sha256", hashlib.sha256(frozen_bytes).hexdigest(), "--future-bundle", str(future), "--future-sha256", future_sha, "--output", str(tmp_path/"result.json")], capture_output=True, text=True, encoding="utf-8")
    assert second.returncode == 0, second.stderr
    assert "точность" in second.stdout and output.read_bytes() == frozen_bytes


def test_no_caller_exact_availability_claim_survives_normalization(tmp_path):
    inputs, _ = bundles(("SBER",))
    case = inputs["cases"][0]
    instrument = batch._instrument(json.loads(json.dumps(case["instrument"])))
    series = batch._series(instrument, "D1", case["series"]["D1"])
    assert all(c.available_at is None and c.available_at_confidence == "UNKNOWN" for c in series.candles)


@pytest.mark.parametrize("timeframe", ["H1", "M15"])
def test_frozen_batch_rejects_any_2024_intraday_without_writing(tmp_path, timeframe):
    inputs, _ = bundles(("SBER",))
    first = inputs["cases"][0]["series"][timeframe][0]
    first["begin"] = "2024-12-31T10:00:00+03:00"
    first["end"] = "2024-12-31T10:59:59+03:00" if timeframe == "H1" else "2024-12-31T10:14:59+03:00"
    source, out = tmp_path/"input.json", tmp_path/"sealed.json"
    sha = store(source, inputs)
    with pytest.raises(ValueError, match="2025-26"):
        batch.freeze_batch(source, bundle_sha256=sha, output=out)
    assert not out.exists()


def test_batch_rejects_2027_future_calendar_without_changing_frozen_predictions(tmp_path):
    inputs, futures = bundles(("SBER",))
    _, predictions, _, pred_sha = freeze(tmp_path, inputs)
    before = predictions.read_bytes()
    futures["cases"][0]["expected_session_dates"][-1] = "2027-01-04"
    with pytest.raises(ValueError, match="2025-26"):
        assess(tmp_path, predictions, pred_sha, futures)
    assert predictions.read_bytes() == before
    assert not (tmp_path/"results.json").exists()


def test_assessment_rejects_legacy_2021_frozen_prediction_even_with_rehashed_seal(tmp_path):
    inputs, futures = bundles(("SBER",))
    _, predictions, frozen, _ = freeze(tmp_path, inputs)
    # A historic frozen file produced by the old 2021-22 workflow cannot be
    # admitted merely because its outer SHA and inner SHA are self-consistent.
    legacy = deepcopy(frozen)
    replay = legacy["predictions"][0]["replay"]
    replay["requested_cutoff_at"] = "2021-10-10T23:59:59+03:00"
    replay["replay_sha256"] = batch._report_sha(replay)
    sha = store(predictions, legacy)
    with pytest.raises(ValueError, match="inside 2025-26"):
        assess(tmp_path, predictions, sha, futures)
    assert not (tmp_path/"results.json").exists()
