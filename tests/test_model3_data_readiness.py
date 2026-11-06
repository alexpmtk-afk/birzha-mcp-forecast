"""Isolated, source-only RI Model3 data readiness tests.

No real trades, no Google writes, no out-of-sample signals.
"""
from copy import deepcopy

import pytest

from birzha.application.model3_data_readiness import readiness_summary
from birzha.application.model3_indicator_coverage import COMPACT_RI_INDICATOR_COLUMNS


RAW_HEADER = (
    "record_key", "calendar_date", "trade_session_date", "bar_start_time",
    "bar_end_time", "available_at", "contract_code", "open", "high",
    "low", "close", "volume", "is_complete", "source_id", "research_status",
    "normalized_sha256", "caveat", "source_file_id",
)
KEY = "RI|H1|RIH6|2026-01-05T10:00:00+03:00"


def sample():
    raw = [
        list(RAW_HEADER),
        [KEY, "2026-01-05", "2026-01-05",
         "2026-01-05T10:00:00+03:00", "2026-01-05T10:59:59+03:00",
         "", "RIH6", 110000, 110300, 109700, 110200, 950, "TRUE",
         "MOEX_ISS", "RECONSTRUCTED_RESEARCH_ONLY", "abc123", "", "source-zip"],
    ]
    header = list(COMPACT_RI_INDICATOR_COLUMNS)
    values = [""] * len(header)
    def put(name, value):
        values[header.index(name)] = value
    put("record_key", "RI|RIH6|H1|2026-01-05T10:00:00+03:00|P3_OBS_V1")
    put("symbol", "RI")
    put("secid", "RIH6")
    put("timeframe", "H1")
    put("source_record_key", KEY)
    put("source_complete_flag", "TRUE")
    put("quality_status", "RECONSTRUCTED_RESEARCH_ONLY")
    put("available_at_confidence", "UNKNOWN")
    put("calendar_status", "CALENDAR_UNVERIFIED")
    put("p3_range", 600)
    put("p3_close_location", 0.83333333)
    put("p3_range_prior_atr", "")
    put("p3_rvol_same_clock20_approx", 1.7)
    put("p3_candidate_state", "BULL_INITIATIVE_CANDIDATE")
    put("p3_structure", "HH_HL")
    put("p3_minute_sign_delta_proxy", 17)
    put("p3_delta_proxy_status", "M1_CLOSE_SIGN;NOT_AGGRESSOR")
    put("p3_profile_poc_approx", 110050)
    put("p3_profile_status", "APPROX")
    put("p3_signal_eligible", "FALSE")
    return raw, [header, values]


def test_counts_are_present_but_no_forecast_attestation():
    raw, ind = sample()
    result = readiness_summary(raw, ind, timeframe="H1")
    assert result["status"] == "READ_ONLY_RESEARCH_DATA_READINESS"
    assert result["source_records"] == result["indicator_records"] == 1
    assert result["record_coverage"]["missing_indicator_records"] == 0
    assert result["raw_with_trade_session_date"] == 1
    assert result["raw_with_available_at_value_not_attested"] == 0
    assert result["feature_nonempty_counts"]["p3_range"] == 1
    assert result["feature_nonempty_counts"]["p3_rvol_same_clock20_approx"] == 1
    assert result["feature_nonempty_counts"]["p3_range_prior_atr"] == 0
    assert result["candidate_state_counts_observation_only"] == {"BULL_INITIATIVE_CANDIDATE": 1}
    assert result["signal_eligible_claims_not_admitted"] == 0
    assert result["data_gates"]["exact_aggressor_delta"] == "UNAVAILABLE"
    assert result["data_gates"]["exact_volume_at_price"] == "UNAVAILABLE"
    assert result["data_gates"]["forecast_admission"] == "NOT_GRANTED"
    assert result["written"] is False


def test_explicit_incomplete_raw_stays_incomplete_even_with_indicator_row():
    raw, ind = sample()
    raw[1][RAW_HEADER.index("is_complete")] = "FALSE"
    ind[1][ind[0].index("source_complete_flag")] = "FALSE"
    result = readiness_summary(raw, ind, timeframe="H1")
    assert result["raw_complete"] == {"incomplete": 1}
    assert result["indicator_complete"] == {"incomplete": 1}
    assert result["indicator_source_completeness_disagreements"] == 0


def test_completeness_mismatch_is_reported_not_auto_repaired():
    raw, ind = sample()
    raw[1][RAW_HEADER.index("is_complete")] = "FALSE"
    result = readiness_summary(raw, ind, timeframe="H1")
    assert result["indicator_source_completeness_disagreements"] == 1


def test_claimed_eligible_never_grants_admission():
    raw, ind = sample()
    ind[1][ind[0].index("p3_signal_eligible")] = "TRUE"
    raw[1][RAW_HEADER.index("available_at")] = "2026-01-05T11:00:10+03:00"
    result = readiness_summary(raw, ind, timeframe="H1")
    assert result["signal_eligible_claims_not_admitted"] == 1
    assert result["raw_with_available_at_value_not_attested"] == 1
    assert result["data_gates"]["historical_PIT_admission"] is False
    assert result["data_gates"]["forecast_admission"] == "NOT_GRANTED"


def test_duplicate_key_rejected_before_quality_counts():
    raw, ind = sample()
    raw.append(deepcopy(raw[1]))
    with pytest.raises(ValueError, match="CANDLE_SOURCE_IDENTITY_OR_DUPLICATE"):
        readiness_summary(raw, ind, timeframe="H1")


def test_invalid_boolean_fails_closed():
    raw, ind = sample()
    ind[1][ind[0].index("p3_signal_eligible")] = "UNKNOWN"
    with pytest.raises(ValueError, match="MODEL3_BOOLEAN_FIELD_INVALID"):
        readiness_summary(raw, ind, timeframe="H1")
