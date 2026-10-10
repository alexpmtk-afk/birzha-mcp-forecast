"""Synthetic artifacts only; no real archive, reserved data, or calibration."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
import json

import pytest

from birzha.application.d1_research_dataset_adapter import (
    DATASET_VERSION, MARKETS, DatasetValidationError, canonical_json,
    read_d1_research_dataset, sha256, validate_d1_research_dataset,
)
from birzha.application.d1_research_features import (
    D1_RESEARCH_ATR_METHOD, D1_RESEARCH_FEATURE_VERSION,
)
from birzha.application.d1_research_regime import RegimeParameters
from birzha.application.d1_research_runner import run_d1_descriptive_research


def params():
    return RegimeParameters("synthetic-only", 3., .6, 1., .2, 4.)


def fixtures():
    """Calendar dates here are synthetic; they make no exchange-calendar claim."""
    days = [(date(2021, 1, 1) + timedelta(days=i)).isoformat() for i in range(21)]
    rows = []
    for market in MARKETS:
        secid = {"Si": "SiH1", "BR": "BRH1", "GOLD": "GDH1"}.get(market, market)
        feature_values = {
            name: {"value": value, "status": "AVAILABLE", "reason": None}
            for name, value in (
                ("atr14_sma_tr", 2.), ("d20_atr", 5.), ("er20", .8), ("w20_atr", 6.),
            )
        }
        feature_values["profile_poc"] = {
            "value": None, "status": "UNAVAILABLE", "reason": "not_supplied",
        }
        feature_values["volume_ratio20"] = {
            "value": None, "status": "NOT_APPLICABLE", "reason": "no_volume_capability",
        }
        rows.append({
            "market": market, "secid": secid, "session": days[-1],
            "bar_event_end_at": days[-1] + "T23:49:59",
            "decision_knowledge_cutoff_at": None, "historical_first_receipt": "NOT_PROVEN",
            "calendar_evidence_origin": "ARCHIVED_ACTIVE_ROOT_CALENDAR",
            "calendar_evidence_key": "synthetic:" + market,
            "expected_sessions": days.copy(), "source": "HISTORICAL_ARCHIVE_RECONSTRUCTED",
            "features": {
                "schema": D1_RESEARCH_FEATURE_VERSION,
                "secid": secid, "session": days[-1],
                "evidence_origin": "ARCHIVED_ACTIVE_ROOT_CALENDAR",
                "evidence_key": "synthetic:" + market,
                "observed_bars": 21, "atr_method": D1_RESEARCH_ATR_METHOD,
                "eligibility": "RECONSTRUCTED_RESEARCH_ONLY",
                "strict_historical_as_known_at_t0": False, "features": feature_values,
            },
        })
    transition = deepcopy(rows[0])
    transition["session"] = transition["features"]["session"] = "2021-01-22"
    transition["bar_event_end_at"] = "2021-01-22T23:49:59"
    transition["expected_sessions"] = days[1:] + ["2021-01-22"]
    for key, value in (("d20_atr", 2.), ("er20", .4), ("w20_atr", 5.)):
        transition["features"]["features"][key]["value"] = value
    rows.append(transition)
    excluded = [{"market": "IMOEX", "secid": "IMOEX", "session": "2021-01-23",
                 "reason": "UNVERIFIED_EXTRA_OR_MISSING_D1_SESSION"}]
    totals = {
        m: {
            "development_active_candidates": 2 if m in ("SBER", "IMOEX") else 1,
            "admitted_reconstructed": 2 if m == "SBER" else 1,
            "excluded": 1 if m == "IMOEX" else 0,
            "exclusion_reasons": {"UNVERIFIED_EXTRA_OR_MISSING_D1_SESSION": 1}
                if m == "IMOEX" else {},
        }
        for m in MARKETS
    }
    manifest = {
        "schema": DATASET_VERSION, "scope": "RECONSTRUCTED_RESEARCH_ONLY",
        "strict_historical_as_known_at_T0": False, "feature_version": D1_RESEARCH_FEATURE_VERSION,
        "markets": list(MARKETS), "development_from": "2021-01-01",
        "development_till": "2022-12-31", "accepted_rows": len(rows),
        "excluded_rows": len(excluded), "per_market": totals,
        "source_sha256_before_after": "a" * 64, "v2_report_sha256": "b" * 64,
        "extractor_code_sha256": "c" * 64, "feature_code_sha256": "d" * 64,
        "warnings": ["SYNTHETIC; not a real admission certificate"],
    }
    return manifest, rows, excluded


def encode(manifest, rows, excluded):
    accepted = "".join(canonical_json(row) + "\n" for row in rows).encode()
    exclusions = "".join(canonical_json(row) + "\n" for row in excluded).encode()
    manifest = deepcopy(manifest)
    manifest.update(accepted_rows_sha256=sha256(accepted),
                    exclusion_rows_sha256=sha256(exclusions))
    return (canonical_json(manifest) + "\n").encode(), accepted, exclusions


def validate(payload):
    return validate_d1_research_dataset(*payload, trusted_manifest_sha256=sha256(payload[0]))


def change(target, path, value):
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def test_readonly_adapter_preserves_null_reason_and_origin(tmp_path):
    payload = encode(*fixtures())
    paths = [tmp_path / name for name in ("manifest.json", "rows.jsonl", "exclusions.jsonl")]
    for path, content in zip(paths, payload):
        path.write_bytes(content)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = read_d1_research_dataset(*paths, trusted_manifest_sha256=sha256(payload[0]))
    assert len(result.rows) == 7
    assert len(result.exclusions_json) == 1
    optional = dict(result.rows[0].features.features)["profile_poc"]
    assert (optional.value, optional.status, optional.reason) == (None, "UNAVAILABLE", "not_supplied")
    assert result.rows[0].features.evidence_key == "synthetic:SBER"
    assert result.manifest_sha256 == sha256(payload[0])
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    with pytest.raises(FrozenInstanceError):
        result.rows = ()


@pytest.mark.parametrize("field,value", [
    ("schema", "future-version"), ("scope", "STRICT"),
    ("strict_historical_as_known_at_T0", True), ("strict_historical_as_known_at_T0", 0),
    ("feature_version", "wrong"), ("markets", list(MARKETS[:-1])),
    ("markets", ["SBER"] * 6), ("markets", [None] * 6),
    ("development_from", "2020-01-01"), ("development_till", "2023-01-01"),
    ("development_from", "2022-12-32"), ("accepted_rows", True),
    ("accepted_rows", 8), ("excluded_rows", -1), ("excluded_rows", 0),
    ("source_sha256_before_after", "missing"), ("feature_code_sha256", 123),
    ("warnings", None), ("per_market", {}),
])
def test_manifest_contract_rejects_even_when_external_hash_is_valid(field, value):
    manifest, rows, excluded = fixtures()
    manifest[field] = value
    with pytest.raises(DatasetValidationError):
        validate(encode(manifest, rows, excluded))


@pytest.mark.parametrize("path,value", [
    (("market",), "UNKNOWN"),
    (("secid",), "SiH1"),
    (("session",), "2023-01-01"),
    (("session",), "20210121"),
    (("expected_sessions",), ["2021-01-21"] * 21),
    (("expected_sessions",), ["2021-01-21"] * 20),
    (("expected_sessions",), None),
    (("bar_event_end_at",), "2021-01-20T23:49:59"),
    (("bar_event_end_at",), "2021-01-21"),
    (("bar_event_end_at",), "invalid"),
    (("decision_knowledge_cutoff_at",), "2021-01-21T23:49:59"),
    (("historical_first_receipt",), "PROVEN"),
    (("source",), "LIVE"),
    (("calendar_evidence_origin",), "invented"),
    (("calendar_evidence_key",), " "),
    (("features", "schema"), "wrong"),
    (("features", "atr_method"), "WILDER"),
    (("features", "secid"), "OTHER"),
    (("features", "session"), "2021-01-20"),
    (("features", "evidence_origin"), "RECONSTRUCTED_MOEX"),
    (("features", "evidence_key"), "other"),
    (("features", "observed_bars"), True),
    (("features", "observed_bars"), 20),
    (("features", "eligibility"), "STRICT"),
    (("features", "strict_historical_as_known_at_t0"), 0),
    (("features", "features", "atr14_sma_tr", "value"), 0),
    (("features", "features", "er20", "value"), 1.1),
    (("features", "features", "er20", "value"), -.1),
    (("features", "features", "w20_atr", "value"), -1),
    (("features", "features", "d20_atr", "value"), True),
    (("features", "features", "d20_atr", "value"), "5"),
    (("features", "features", "d20_atr", "value"), 10 ** 400),
    (("features", "features", "d20_atr", "value"), None),
    (("features", "features", "er20", "status"), "UNAVAILABLE"),
    (("features", "features", "profile_poc", "value"), 0),
    (("features", "features", "profile_poc", "status"), "OTHER"),
    (("features", "features", "profile_poc", "reason"), {}),
])
def test_row_contract_rejects_without_silently_dropping_row(path, value):
    manifest, rows, excluded = fixtures()
    change(rows[0], path, value)
    with pytest.raises(DatasetValidationError):
        validate(encode(manifest, rows, excluded))


@pytest.mark.parametrize("market,secid", [("GOLD", "GOLD"), ("GOLD", "SiH1"),
                                        ("Si", "BRH1"), ("BR", "BR")])
def test_futures_require_exact_family(market, secid):
    manifest, rows, excluded = fixtures()
    row = next(row for row in rows if row["market"] == market)
    row["secid"] = row["features"]["secid"] = secid
    with pytest.raises(DatasetValidationError, match="family"):
        validate(encode(manifest, rows, excluded))


@pytest.mark.parametrize("field,value", [
    ("development_active_candidates", 3), ("admitted_reconstructed", 1),
    ("excluded", 1), ("exclusion_reasons", {"made_up": 1}),
    ("admitted_reconstructed", True),
])
def test_per_market_counts_must_reconcile(field, value):
    manifest, rows, excluded = fixtures()
    manifest["per_market"]["SBER"][field] = value
    with pytest.raises(DatasetValidationError):
        validate(encode(manifest, rows, excluded))


@pytest.mark.parametrize("field,value", [
    ("market", "OTHER"), ("session", "2023-01-01"), ("secid", ""),
    ("reason", ""), ("reason", None),
])
def test_exclusions_are_validated_and_never_lost(field, value):
    manifest, rows, excluded = fixtures()
    excluded[0][field] = value
    with pytest.raises(DatasetValidationError):
        validate(encode(manifest, rows, excluded))


@pytest.mark.parametrize("mode", ["accepted_duplicate", "cross_file_duplicate",
                                  "missing_row", "missing_feature", "missing_null"])
def test_duplicates_and_missing_content(mode):
    manifest, rows, excluded = fixtures()
    if mode == "accepted_duplicate":
        rows[1] = deepcopy(rows[0])
    elif mode == "cross_file_duplicate":
        excluded[0].update(market=rows[0]["market"], session=rows[0]["session"])
    elif mode == "missing_row":
        rows.pop()
    elif mode == "missing_feature":
        del rows[0]["features"]["features"]["er20"]
    else:
        del rows[0]["decision_knowledge_cutoff_at"]
    with pytest.raises(DatasetValidationError):
        validate(encode(manifest, rows, excluded))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_byte_tampering_rejected(index):
    payload = list(encode(*fixtures()))
    trust = sha256(payload[0])
    payload[index] += b" "
    with pytest.raises(DatasetValidationError, match="SHA"):
        validate_d1_research_dataset(*payload, trusted_manifest_sha256=trust)


def test_rehashing_changed_files_and_manifest_does_not_replace_external_trust():
    manifest, rows, excluded = fixtures()
    original = encode(manifest, rows, excluded)
    rows[0]["features"]["features"]["d20_atr"]["value"] = 6
    changed = encode(manifest, rows, excluded)
    with pytest.raises(DatasetValidationError, match="trusted manifest SHA"):
        validate_d1_research_dataset(*changed, trusted_manifest_sha256=sha256(original[0]))


@pytest.mark.parametrize("replacement", [
    b"\xff\n", b"{}\n\n", b"{bad}\n", b"[]\n", b'{"x":1,"x":2}\n',
    b'{"x":NaN}\n', b'{"x":Infinity}\n', b'{"x":1e999}\n', b"{}",
])
def test_bad_jsonl_rejected_even_with_matching_byte_hash(replacement):
    payload = list(encode(*fixtures()))
    manifest = json.loads(payload[0])
    manifest["accepted_rows_sha256"] = sha256(replacement)
    payload[0] = canonical_json(manifest).encode()
    payload[1] = replacement
    with pytest.raises(DatasetValidationError):
        validate(payload)


def test_trusted_hash_required_and_must_be_valid():
    payload = encode(*fixtures())
    with pytest.raises(TypeError):
        validate_d1_research_dataset(*payload)
    with pytest.raises(DatasetValidationError):
        validate_d1_research_dataset(*payload, trusted_manifest_sha256="")


def test_warmup_before_development_is_not_silently_discarded():
    manifest, rows, excluded = fixtures()
    row = rows[0]
    days = [(date(2020, 12, 15) + timedelta(days=i)).isoformat() for i in range(21)]
    row["expected_sessions"] = days
    row["session"] = row["features"]["session"] = days[-1]
    row["bar_event_end_at"] = days[-1] + "T23:49:59"
    result = validate(encode(manifest, rows, excluded))
    assert json.loads(result.rows[0].original_json)["expected_sessions"] == days


def test_excluded_wrong_identity_is_preserved_as_an_exclusion():
    manifest, rows, excluded = fixtures()
    excluded[0]["secid"] = "REJECTED_IDENTITY"
    result = validate(encode(manifest, rows, excluded))
    assert json.loads(result.exclusions_json[0])["secid"] == "REJECTED_IDENTITY"


def test_complete_run_deterministic_retains_inputs_and_counts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = encode(*fixtures())
    kwargs = dict(trusted_manifest_sha256=sha256(payload[0]), parameters=params())
    first = run_d1_descriptive_research(*payload, **kwargs)
    assert first == run_d1_descriptive_research(*payload, **kwargs)
    result = json.loads(first)
    assert result["completion"] == "COMPLETE"
    assert result["accepted_result_count"] == 7
    assert result["upstream_exclusion_count"] == 1
    assert result["per_market"]["SBER"]["UNKNOWN_TRANSITION"] == 1
    assert result["per_market"]["SBER"]["TREND_UP"] == 1
    assert result["per_market"]["IMOEX"]["upstream_excluded"] == 1
    assert all(m["invalid_inputs"] == 0 for m in result["per_market"].values())
    assert result["parameter_fingerprint"] == params().fingerprint
    assert result["input_manifest"]["accepted_rows_sha256"] == sha256(payload[1])
    assert result["results"][0]["input"]["decision_knowledge_cutoff_at"] is None
    assert result["results"][0]["input"]["features"]["features"]["profile_poc"]["reason"] == "not_supplied"
    assert result["results"][0]["regime"]["scope"] == "RECONSTRUCTED_RESEARCH_ONLY"
    assert list(tmp_path.iterdir()) == []


def test_runner_requires_parameters():
    payload = encode(*fixtures())
    with pytest.raises(TypeError):
        run_d1_descriptive_research(*payload, trusted_manifest_sha256=sha256(payload[0]))
    with pytest.raises(ValueError, match="explicit"):
        run_d1_descriptive_research(*payload, trusted_manifest_sha256=sha256(payload[0]), parameters=None)


def test_invalid_late_row_aborts_before_any_classification(tmp_path, monkeypatch):
    import birzha.application.d1_research_runner as runner
    monkeypatch.chdir(tmp_path)
    def must_not_run(*args, **kwargs):
        pytest.fail("classification started before full-bundle validation")
    monkeypatch.setattr(runner, "classify_d1_research_regime", must_not_run)
    manifest, rows, excluded = fixtures()
    rows[-1]["features"]["features"]["er20"]["value"] = 2
    payload = encode(manifest, rows, excluded)
    with pytest.raises(DatasetValidationError):
        runner.run_d1_descriptive_research(
            *payload, trusted_manifest_sha256=sha256(payload[0]), parameters=params())
    assert list(tmp_path.iterdir()) == []


def test_existing_feature_builder_output_adapts_without_schema_translation_guess():
    from birzha.application.d1_research_features import build_d1_research_features
    from birzha.domain.market import Candle, CandleSeries, Instrument
    manifest, rows, excluded = fixtures()
    for row in rows:
        inst = Instrument(symbol=row["market"], secid=row["secid"], board="SYNTHETIC",
                          engine="stock", market="shares", asset_class="equity",
                          data_capabilities=("CANDLES",))
        bars = tuple(Candle(open=100.+i, close=100.+i, high=102.+i, low=98.+i,
                            volume=0., value=0., begin=day+"T00:00:00",
                            end=day+"T23:49:59", completed=True, source="SYNTHETIC")
                     for i, day in enumerate(row["expected_sessions"]))
        row["features"] = build_d1_research_features(
            CandleSeries(inst, "D1", bars), exact_secid=row["secid"],
            expected_sessions=tuple(row["expected_sessions"]),
            evidence_origin=row["calendar_evidence_origin"],
            evidence_key=row["calendar_evidence_key"],
        ).to_dict()
    result = validate(encode(manifest, rows, excluded))
    assert len(result.rows) == len(rows)
    for original, adapted in zip(rows, result.rows):
        assert adapted.features.to_dict() == original["features"]


def test_classifier_internal_failure_cannot_return_partial_output(tmp_path, monkeypatch):
    import birzha.application.d1_research_runner as runner
    monkeypatch.chdir(tmp_path)
    original = runner.classify_d1_research_regime
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("synthetic internal failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(runner, "classify_d1_research_regime", fail_second)
    payload = encode(*fixtures())
    with pytest.raises(ValueError, match="synthetic internal failure"):
        runner.run_d1_descriptive_research(
            *payload, trusted_manifest_sha256=sha256(payload[0]), parameters=params())
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("state,direction", [
    ("BROKEN", None), ("BALANCE", "UP"), ("TREND", None), ("TREND", "SIDEWAYS"),
    ("UNKNOWN", "UP"),
])
def test_invalid_classifier_state_or_direction_aborts(state, direction, tmp_path, monkeypatch):
    from dataclasses import replace
    import birzha.application.d1_research_runner as runner
    monkeypatch.chdir(tmp_path)
    original = runner.classify_d1_research_regime
    def broken(*args, **kwargs):
        result = replace(original(*args, **kwargs), state=state, direction=direction)
        if state == "UNKNOWN":
            result = replace(result, reasons=("OUTSIDE_DECLARED_REGIME_HYPOTHESES",))
        return result
    monkeypatch.setattr(runner, "classify_d1_research_regime", broken)
    payload = encode(*fixtures())
    with pytest.raises(ValueError, match="classifier"):
        runner.run_d1_descriptive_research(
            *payload, trusted_manifest_sha256=sha256(payload[0]), parameters=params())
    assert list(tmp_path.iterdir()) == []
