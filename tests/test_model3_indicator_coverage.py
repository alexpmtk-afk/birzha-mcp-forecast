import pytest
from birzha.application.model3_features import COLUMNS
from birzha.application.model3_indicator_coverage import (
    COMPACT_RI_INDICATOR_COLUMNS, indicator_header_type,
    project_calculated_rows, coverage_summary,
)

def test_compact_ri_95_is_exact_ordered_subset_of_current_calculator():
    h = COMPACT_RI_INDICATOR_COLUMNS
    assert len(h) == 95
    assert h[:9] == (
        "record_key", "symbol", "secid", "timeframe", "begin", "end",
        "source_record_key", "source_record_sha256", "source_window_count")
    assert h[-1] == "p3_cumulative_delta_proxy_civil_day"
    assert indicator_header_type(h) == "COMPACT_RI_95"
    assert indicator_header_type(tuple(reversed(h))) is None
    assert indicator_header_type(h[:-1]) is None

def test_projection_does_not_widen_or_mutate_a_live_compact_header():
    full = ["value:" + name for name in COLUMNS]
    header = list(COMPACT_RI_INDICATOR_COLUMNS)
    result = project_calculated_rows([full], header)
    assert len(result[0]) == 95
    assert result[0][header.index("p3_parameters_version")] == "value:p3_parameters_version"
    assert result[0][header.index("atr_14_wilder")] == "value:atr_14_wilder"
    assert len(header) == 95
    assert len(full) == len(COLUMNS)
    with pytest.raises(ValueError, match="HEADER"):
        project_calculated_rows([full], header[:-1])
    with pytest.raises(ValueError, match="WIDTH"):
        project_calculated_rows([full[:-1]], header)

def _sample():
    source = [
        ["record_key", "contract_code", "bar_start_time"],
        ["RI|D1|RIH5|2025-01-03", "RIH5", "2025-01-03"],
        ["RI|D1|RIH6|2026-01-05", "RIH6", "2026-01-05"],
    ]
    h = list(COMPACT_RI_INDICATOR_COLUMNS)
    row = [""] * len(h)
    for field, value in (
        ("record_key", "RI|RIH6|D1|2026-01-05|P3_OBS_V1"),
        ("symbol", "RI"), ("secid", "RIH6"), ("timeframe", "D1"),
        ("source_record_key", source[2][0]),
    ):
        row[h.index(field)] = value
    return source, [h, row]

def test_read_only_coverage_detects_2025_gap_without_asserting_formula_readiness():
    src, features = _sample()
    before = [r[:] for r in features]
    report = coverage_summary(src, features, timeframe="D1")
    assert report["indicator_columns"] == 95
    assert report["candle_records"] == 2
    assert report["matched_records"] == 1
    assert report["missing_indicator_records"] == 1
    assert report["missing_record_keys_sample"] == [src[1][0]]
    assert report["forecast_admission"] == "NOT_GRANTED"
    assert report["formula_version_checked"] is False
    assert features == before

def test_coverage_fail_closed_on_duplicate_and_header_drift():
    src, features = _sample()
    with pytest.raises(ValueError, match="DUPLICATE"):
        coverage_summary(src + [src[1]], features, timeframe="D1")
    with pytest.raises(ValueError, match="DUPLICATE"):
        coverage_summary(src, features + [features[1]], timeframe="D1")
    with pytest.raises(ValueError, match="HEADER"):
        coverage_summary(src, [features[0][:-1], features[1][:-1]], timeframe="D1")

def test_archive_service_uses_existing_tabs_and_no_writer():
    from birzha.application.google_first_archive import GoogleFirstArchive
    src, indicators = _sample()
    class ReadOnlyTransport:
        def __init__(self):
            self.calls = []
        def validate_model3_target(self, symbol, sheet):
            self.calls.append(("validate", symbol, sheet))
        def read_rows(self, symbol, sheet):
            self.calls.append(("read", symbol, sheet))
            assert sheet == "D1_INDICATORS"
            return [r[:] for r in indicators]
    transport = ReadOnlyTransport()
    service = GoogleFirstArchive(transport=transport, market=None, source_sha="a" * 40)
    service._schemas = lambda symbol, timeframe: src
    result = service.model3_coverage(timeframe="D1")
    assert result["missing_indicator_records"] == 1
    assert result["written"] is False
    assert transport.calls == [("validate", "RI", "D1_INDICATORS"), ("read", "RI", "D1_INDICATORS")]
    with pytest.raises(Exception, match="TIMEFRAME_UNSUPPORTED"):
        service.model3_coverage(timeframe="M1")
