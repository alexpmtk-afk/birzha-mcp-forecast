"""Two-stage, offline price-engine research: freeze first, assess separately.

Input is an explicitly reconstructed normalized bundle, never an authenticated
original provider receipt. SHA checks bind bytes; they do not certify provenance.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from birzha.domain.market import Candle, CandleSeries, Instrument
from scripts.g2_forecast_historical_replay import (
    ENGINE_VERSION, MOSCOW, TIMEFRAMES, _report_sha, _when,
    evaluate_replay_outcomes, replay_existing_forecast,
)

MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
SCOPE = "RECONSTRUCTED_HISTORICAL_PRICE_ENGINE_ONLY"
INPUT_SCHEMA = "G2_PRICE_REPLAY_INPUT_BUNDLE_V1"
FUTURE_SCHEMA = "G2_PRICE_REPLAY_FUTURE_BUNDLE_V1"
PREDICTION_SCHEMA = "G2_PRICE_REPLAY_FROZEN_BATCH_V1"
RESULT_SCHEMA = "G2_PRICE_REPLAY_ASSESSMENT_BATCH_V1"
MAX_BYTES = 64 * 1024 * 1024
MAX_CASES = 1000


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read(path: Path, expected_sha: str):
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("explicit lowercase SHA-256 required")
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("missing or oversized input file")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("input bytes do not match pinned SHA-256")
    def invalid_constant(value):
        raise ValueError("nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=invalid_constant)


def _keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError("unexpected or missing bundle fields")


def _cases(bundle, schema, *, allow_empty=False):
    _keys(bundle, ("schema", "scope", "cases"))
    if bundle["schema"] != schema or bundle["scope"] != SCOPE:
        raise ValueError("explicit reconstructed research bundle required")
    cases = bundle["cases"]
    if not isinstance(cases, list) or not (0 if allow_empty else 1) <= len(cases) <= MAX_CASES:
        raise ValueError("bounded nonempty case list required")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case object required")
        cid = case.get("case_id")
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", cid) or cid in ids:
            raise ValueError("unique simple case_id required")
        ids.add(cid)
    return cases


def _instrument(value):
    if not isinstance(value, dict):
        raise ValueError("instrument object required")
    allowed = {f.name for f in fields(Instrument)}
    if set(value) - allowed or not {"symbol", "secid", "board", "engine", "market", "asset_class"} <= set(value):
        raise ValueError("explicit instrument identity required")
    for key in ("symbol", "secid", "board", "engine", "market", "asset_class"):
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError("invalid instrument identity")
    if value["symbol"] not in MARKETS:
        raise ValueError("unsupported project market")
    caps = value.get("data_capabilities", [])
    if not isinstance(caps, list) or any(not isinstance(c, str) for c in caps):
        raise ValueError("capability list required")
    normalized = dict(value, data_capabilities=tuple(caps))
    return Instrument(**normalized)


def _series(instrument, timeframe, rows):
    if not isinstance(rows, list):
        raise ValueError("explicit candle list required, empty means missing")
    allowed = {f.name for f in fields(Candle)}
    required = {"open", "close", "high", "low", "volume", "value", "begin", "end", "completed"}
    candles = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - allowed or not required <= set(row):
            raise ValueError("explicit normalized OHLC and completion required")
        for key in ("begin", "end"):
            day = _when(row[key]).astimezone(MOSCOW).date().isoformat()
            if not "2025-01-01" <= day <= "2026-12-31":
                raise ValueError("all candle prices must remain inside intraday replay period 2025-26")
        # Do not propagate caller-provided EXACT claims into reconstructed data.
        clean = dict(row, available_at=None, available_at_confidence="UNKNOWN",
                     observed_at=None, source="NORMALIZED_RECONSTRUCTED_BUNDLE")
        candles.append(Candle(**clean))
    return CandleSeries(instrument, timeframe, tuple(candles),
                        source="NORMALIZED_RECONSTRUCTED_BUNDLE")


def _preflight_output(output, inputs):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("output already exists; frozen files cannot be overwritten")
    if not output.parent.is_dir() or output.resolve() in {Path(p).resolve() for p in inputs}:
        raise ValueError("output must be a new file in an existing directory")
    return output


def _save(output, value):
    raw = _canonical(value) + b"\n"
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".g2-batch-", delete=False) as stream:
            name = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # No replacement, even if another process creates the destination late.
        os.link(name, output)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def freeze_batch(bundle_path, *, bundle_sha256, output):
    """Compute and freeze predictions without reading any future bundle."""
    source = Path(bundle_path)
    output = _preflight_output(output, (source,))
    cases = _cases(_read(source, bundle_sha256), INPUT_SCHEMA)
    predictions, identities = [], set()
    for case in cases:
        _keys(case, ("case_id", "instrument", "cutoff_at", "series"))
        instrument = _instrument(case["instrument"])
        _keys(case["series"], TIMEFRAMES)
        identity = (instrument.symbol, instrument.secid, _when(case["cutoff_at"]))
        if identity in identities:
            raise ValueError("same market/contract/cutoff cannot count twice")
        identities.add(identity)
        series = {tf: _series(instrument, tf, case["series"][tf]) for tf in TIMEFRAMES}
        replay = replay_existing_forecast(instrument, cutoff_at=case["cutoff_at"],
                                          series=series, source_sha256=bundle_sha256)
        predictions.append({"case_id": case["case_id"], "replay": replay})
    result = {"schema": PREDICTION_SCHEMA, "scope": SCOPE,
              "engine_version": ENGINE_VERSION, "input_bundle_sha256": bundle_sha256,
              "predictions": predictions, "journal_written": False, "release_allowed": False,
              "historical_first_receipt": "NOT_PROVEN", "source_authenticity_verified": False}
    sha = _save(output, result)
    return result, sha


def _unknown_rows():
    return [{"horizon_sessions": h, "status": "PENDING", "actual_return_pct": None,
             "direction_hit": None, "always_up_correct": None,
             "absolute_move_error_percentage_points": None,
             "flat_price_error_percentage_points": None} for h in (5, 10, 20)]


def _summary(rows):
    summary = []
    for market in MARKETS:
        selected = [r for r in rows if r["market"] == market]
        for horizon in (5, 10, 20):
            outcomes = [next(o for o in r["outcomes"] if o["horizon_sessions"] == horizon) for r in selected]
            observed = [o for o in outcomes if o["status"] == "OBSERVED"]
            directional = [o for o in observed if o["direction_hit"] is not None]
            numeric = [o for o in observed if o["absolute_move_error_percentage_points"] is not None]
            hits = sum(o["direction_hit"] for o in directional)
            up = sum(o["always_up_correct"] for o in directional)
            summary.append({"market": market, "horizon_sessions": horizon,
                "requested": len(selected),
                "input_refused": sum(r["assessment_status"] == "MISSING_INPUTS" for r in selected),
                "observed": len(observed), "pending": sum(o["status"] == "PENDING" for o in outcomes),
                "directional": len(directional), "neutral_observed": len(observed)-len(directional),
                "direction_hits": hits if directional else None,
                "always_up_hits": up if directional else None,
                "direction_accuracy_pct": round(hits/len(directional)*100, 8) if directional else None,
                "always_up_accuracy_pct": round(up/len(directional)*100, 8) if directional else None,
                "numeric_pairs": len(numeric),
                "mean_absolute_move_error_pp": sum(o["absolute_move_error_percentage_points"] for o in numeric)/len(numeric) if numeric else None,
                "mean_flat_price_error_pp": sum(o["flat_price_error_percentage_points"] for o in numeric)/len(numeric) if numeric else None})
    return summary


def assess_batch(predictions_path, *, predictions_sha256, future_path, future_sha256, output):
    """Read the already frozen predictions; never rebuild or overwrite them."""
    predictions_path, future_path = Path(predictions_path), Path(future_path)
    output = _preflight_output(output, (predictions_path, future_path))
    frozen = _read(predictions_path, predictions_sha256)
    _keys(frozen, ("schema", "scope", "engine_version", "input_bundle_sha256", "predictions",
                   "journal_written", "release_allowed", "historical_first_receipt", "source_authenticity_verified"))
    if (frozen["schema"] != PREDICTION_SCHEMA or frozen["scope"] != SCOPE
            or frozen["engine_version"] != ENGINE_VERSION or frozen["journal_written"] is not False
            or frozen["release_allowed"] is not False or frozen["source_authenticity_verified"] is not False
            or frozen["historical_first_receipt"] != "NOT_PROVEN"):
        raise ValueError("unchanged research prediction batch required")
    predictions = frozen["predictions"]
    if not isinstance(predictions, list) or not 1 <= len(predictions) <= MAX_CASES:
        raise ValueError("invalid frozen predictions")
    futures = _cases(_read(future_path, future_sha256), FUTURE_SCHEMA, allow_empty=True)
    future_by_id = {}
    for case in futures:
        _keys(case, ("case_id", "instrument", "expected_session_dates", "candles"))
        instrument = _instrument(case["instrument"])
        series = _series(instrument, "D1", case["candles"])
        dates = case["expected_session_dates"]
        if not isinstance(dates, list) or any(not isinstance(d, str) for d in dates):
            raise ValueError("explicit future session list required")
        if (len(dates) != 20 or sorted(set(dates)) != dates
                or any(date.fromisoformat(d).isoformat() != d or not "2025-01-01" <= d <= "2026-12-31" for d in dates)):
            raise ValueError("twenty ordered canonical 2025-26 research session dates required")
        future_by_id[case["case_id"]] = (series, tuple(dates))
    rows, ids = [], set()
    for record in predictions:
        _keys(record, ("case_id", "replay"))
        cid, replay = record["case_id"], record["replay"]
        if not isinstance(cid, str) or cid in ids:
            raise ValueError("unique frozen case ids required")
        ids.add(cid)
        if (replay.get("replay_sha256") != _report_sha(replay)
                or replay.get("source_sha256") != frozen["input_bundle_sha256"]
                or replay.get("scope") != SCOPE or replay.get("market") not in MARKETS
                or replay.get("engine_version") != ENGINE_VERSION
                or replay.get("assessment_status") not in ("MISSING_INPUTS", "ENGINE_REPLAYED")
                or replay.get("release_allowed") is not False or replay.get("journal_written") is not False):
            raise ValueError("original sealed replay required")
        replay_day = _when(replay.get("requested_cutoff_at")).astimezone(MOSCOW).date().isoformat()
        if not "2025-01-01" <= replay_day <= "2026-12-31":
            raise ValueError("frozen Forecast V1 replay T0 must be inside 2025-26")
        outcomes = _unknown_rows()
        status = replay["assessment_status"]
        if status == "MISSING_INPUTS":
            for outcome in outcomes:
                outcome["status"] = "INPUT_REFUSED"
        if cid in future_by_id:
            series, dates = future_by_id[cid]
            if series.instrument.symbol != replay["market"] or series.instrument.secid != replay["secid"]:
                raise ValueError("future instrument differs from frozen prediction")
        if status == "ENGINE_REPLAYED" and cid in future_by_id:
            series, dates = future_by_id[cid]
            outcomes = evaluate_replay_outcomes(replay, future=series, expected_session_dates=dates)["outcomes"]
            status = "ASSESSED" if all(o["status"] == "OBSERVED" for o in outcomes) else "PARTIAL"
        elif status == "ENGINE_REPLAYED":
            status = "FUTURE_NOT_SUPPLIED"
        rows.append({"case_id": cid, "market": replay["market"], "secid": replay["secid"],
                     "cutoff_at": replay["requested_cutoff_at"], "replay_sha256": replay["replay_sha256"],
                     "assessment_status": status, "blockers": replay["blockers"], "outcomes": outcomes})
    if set(future_by_id) - ids:
        raise ValueError("future references unknown frozen prediction")
    result = {"schema": RESULT_SCHEMA, "scope": SCOPE, "engine_version": ENGINE_VERSION,
              "frozen_predictions_sha256": predictions_sha256, "future_bundle_sha256": future_sha256,
              "rows": rows, "summary": _summary(rows), "journal_written": False,
              "release_allowed": False, "independent_quality_proven": False,
              "historical_first_receipt": "NOT_PROVEN", "independent_exchange_calendar_verified": False,
              "warning": "Descriptive reconstructed research; overlapping horizons are not independent samples."}
    sha = _save(output, result)
    return result, sha


def russian_summary(result):
    lines = ["Исследование исторических прогнозов. Независимая точность не доказана."]
    for row in result["summary"]:
        if row["requested"] == 0:
            continue
        accuracy = "не определена" if row["direction_accuracy_pct"] is None else f'{row["direction_accuracy_pct"]:.2f}%'
        lines.append(f'{row["market"]}, {row["horizon_sessions"]} сессий: исходов {row["observed"]}, '
                     f'ожидается {row["pending"]}, отказов из-за данных {row["input_refused"]}, '
                     f'проверенных направлений {row["directional"]}, точность {accuracy}.')
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--bundle", required=True, type=Path)
    freeze.add_argument("--bundle-sha256", required=True)
    assess = commands.add_parser("assess")
    assess.add_argument("--predictions", required=True, type=Path)
    assess.add_argument("--predictions-sha256", required=True)
    assess.add_argument("--future-bundle", required=True, type=Path)
    assess.add_argument("--future-sha256", required=True)
    for command in (freeze, assess):
        command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            result, sha = freeze_batch(args.bundle, bundle_sha256=args.bundle_sha256, output=args.output)
            print(f'Сохранено прогнозных контекстов: {len(result["predictions"])}. Контрольная сумма: {sha}')
        else:
            result, sha = assess_batch(args.predictions, predictions_sha256=args.predictions_sha256,
                future_path=args.future_bundle, future_sha256=args.future_sha256, output=args.output)
            print(russian_summary(result))
            print(f"Контрольная сумма результата: {sha}")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(2, f"Исследование не выполнено: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
