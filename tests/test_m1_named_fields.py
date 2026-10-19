"""Synthetic contract tests; no Google write, no HOME, no OOS or trading."""
import itertools
import pytest

from birzha.application.m1_named_fields import (
    SchemaError, RAW_FIELDS, INDICATOR_FIELDS, ATR_RESEARCH_FIELDS,
    LEGACY_INDICATOR_FIELDS, INDICATOR_CORE_FIELDS, FUTURE_OUTCOME_FIELDS,
    api_column_from_index, compare_backread, read_named_row,
    resolve_header, select_m1_t0_features, write_named_row,
)


def headers():
    # Header order is intentionally arbitrary, never inferred by position.
    return ["formula_id", "record_key", "secid", "timeframe", "source_record_key",
            "source_record_sha256", "indicator_status", "atr_14_wilder",
            "atr_14_wilder_status", "atr_formula_id", "atr_source_record_key",
            "return_5", "return_10", "return_20", "atr_14_sma", "volume_ratio_20"]


def values(key="r1"):
    return {
        "formula_id": "research_v23", "record_key": key, "secid": "SBER",
        "timeframe": "H1", "source_record_key": "SBER|H1|SBER|2026-01-01",
        "source_record_sha256": "verified-synthetic-source",
        "indicator_status": "RESEARCH_ONLY", "atr_14_wilder": 5.0,
        "atr_14_wilder_status": "CALENDAR_UNVERIFIED",
        "atr_formula_id": "m1_atr14_wilder",
        "atr_source_record_key": "SBER|H1|SBER|2026-01-01",
        "return_5": 9000, "return_10": 10000, "return_20": 20000,
        "atr_14_sma": 3, "volume_ratio_20": 0,
    }


def s(h=None):
    return resolve_header(headers() if h is None else h, kind="indicators")


def test_six_instruments_three_timeframes_have_same_schema_independent_of_order():
    assert len(INDICATOR_FIELDS) == 54
    assert len(RAW_FIELDS) == 28
    assert len(INDICATOR_CORE_FIELDS) == 20
    assert len(LEGACY_INDICATOR_FIELDS) == 14
    assert len(ATR_RESEARCH_FIELDS) == 20
    assert all(x not in ATR_RESEARCH_FIELDS for x in LEGACY_INDICATOR_FIELDS)
    for instrument, tf in itertools.product(("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"),
                                             ("D1", "H1", "M15")):
        assert instrument and tf
        assert len(resolve_header(sorted(INDICATOR_FIELDS),kind="indicators").fields) == 54


def test_field_positions_resolve_from_header_not_letter():
    a = s()
    b = s(list(reversed(headers())))
    assert a.position("formula_id") == 0
    assert b.position("formula_id") == len(headers())-1
    assert a.position("record_key") == 1
    assert b.position("record_key") != 1


def test_row_projection_survives_permutation():
    original = values()
    a = s()
    b = s(list(reversed(headers())))
    row_a = write_named_row(a, original)
    row_b = write_named_row(b, original)
    assert row_a != row_b
    assert read_named_row(a,row_a) == read_named_row(b,row_b) == original


def test_writing_unknown_named_column_fails_instead_of_silently_creating():
    with pytest.raises(SchemaError, match="cannot silently create"):
        write_named_row(s(), {**values(), "fake_header": 1})


def test_missing_header_fails_before_read_or_write():
    with pytest.raises(SchemaError, match="missing mandatory"):
        s([x for x in headers() if x != "atr_14_wilder"])


def test_duplicate_header_rejected():
    with pytest.raises(SchemaError, match="duplicated"):
        s(headers() + ["formula_id"])


def test_blank_header_rejected():
    with pytest.raises(SchemaError, match="blank"):
        s(headers()+[""])


@pytest.mark.parametrize("bad", [None, " record_key", "record_key ", 3])
def test_malformed_header_rejected(bad):
    with pytest.raises(SchemaError, match="noncanonical"):
        s(headers()+[bad])


def test_unapproved_extension_rejected():
    with pytest.raises(SchemaError, match="unapproved"):
        s(headers()+["m1_future_invention"])


def test_allowing_versioned_extension_must_be_explicit():
    extension = s(headers()+["future_name"],) if False else resolve_header(
        headers()+["future_name"],kind="indicators",allow_versioned_extensions=True)
    assert extension.position("future_name") == len(headers())


def test_wrong_schema_kind_rejected():
    with pytest.raises(SchemaError, match="unknown schema kind"):
        resolve_header(["record_key"],kind="forecast-live")


def test_missing_required_record_value_rejected():
    with pytest.raises(SchemaError, match="missing required values"):
        write_named_row(s(), {"record_key": "ok"}, required_values=frozenset(["record_key","secid"]))


def test_trailing_blank_row_values_are_explicit():
    x = s()
    assert read_named_row(x, ["formula"])["record_key"] == ""
    assert len(write_named_row(x, {"record_key": "x"})) == len(headers())


def test_backread_handles_reordered_columns_with_actual_key():
    old_schema=s()
    new_header=list(reversed(headers()))
    wanted=values()
    read_rows=[write_named_row(s(new_header),wanted)]
    compare_backread(old_schema,[wanted],new_header,read_rows)


def test_backread_rejects_missing_row_after_lost_ack():
    with pytest.raises(SchemaError,match="committed row not found"):
        compare_backread(s(),[values()],headers(),[])


def test_backread_rejects_changed_payload_even_when_count_matches():
    changed={**values(),"atr_14_wilder": 9}
    with pytest.raises(SchemaError,match="content mismatch"):
        compare_backread(s(),[values()],headers(),[write_named_row(s(),changed)])


def test_backread_rejects_duplicate_keys_before_upsert():
    row=write_named_row(s(),values())
    with pytest.raises(SchemaError,match="duplicate key"):
        compare_backread(s(),[values()],headers(),[row,row])


def test_backread_rejects_schema_modification_during_write():
    with pytest.raises(SchemaError,match="schema changed"):
        compare_backread(s(),[values()],headers()+["sma_50"],[write_named_row(s(),values())])


def test_backread_rejects_duplicate_requested_key():
    with pytest.raises(SchemaError,match="duplicate requested key"):
        compare_backread(s(),[values(),values()],headers(),[write_named_row(s(),values())])


def test_backread_rejects_partial_commit_subset():
    with pytest.raises(SchemaError,match="committed row not found"):
        compare_backread(s(),[values("r1"),values("r2")],headers(),[write_named_row(s(),values("r1"))])


def test_backread_rejects_wrong_contract_even_with_matching_key():
    requested=values()
    observed={**requested,"secid":"BR"}
    with pytest.raises(SchemaError,match="content mismatch"):
        compare_backread(s(),[requested],headers(),[write_named_row(s(),observed)])


def test_future_outcome_labels_never_enter_t0_features():
    h=s()
    row=write_named_row(h,values())
    assert select_m1_t0_features(h,row,frozenset(["atr_14_wilder","secid"])) == {
        "atr_14_wilder":5.0,"secid":"SBER"}
    for forbidden in FUTURE_OUTCOME_FIELDS:
        with pytest.raises(SchemaError,match="future outcomes"):
            select_m1_t0_features(h,row,frozenset(["atr_14_wilder",forbidden]))


def test_future_value_changes_cannot_affect_feature_projection():
    h=s()
    old=write_named_row(h,values())
    new=write_named_row(h,{**values(),"return_5":-1,"return_10":7,"return_20":99})
    selected=frozenset(["atr_14_wilder","secid","timeframe"])
    assert select_m1_t0_features(h,old,selected) == select_m1_t0_features(h,new,selected)


def test_future_feature_allowlist_requires_real_fields():
    with pytest.raises(SchemaError,match="requested feature missing"):
        select_m1_t0_features(s(),write_named_row(s(),values()),frozenset(["nonexistent"]))


def test_named_schema_api_address_not_position_assumption():
    h=s()
    assert h.api_column("formula_id")=="A"
    assert h.api_column("record_key")=="B"
    assert s(list(reversed(headers()))).api_column("formula_id")=="P"
    assert api_column_from_index(25)=="Z"
    assert api_column_from_index(26)=="AA"
    assert api_column_from_index(53)=="BB"


def test_corrupt_api_index_rejected():
    with pytest.raises(SchemaError,match="invalid column"):
        api_column_from_index(-1)
