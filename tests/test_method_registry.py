"""Registry decisions are descriptive evidence, not a way to issue Forecast."""
import pytest

from birzha.application.forecast import ENGINE_VERSION
from birzha.application.method_registry import (
    REGISTRY_VERSION, read_method_decisions,
)
from birzha.application.method_signals import METHOD_SET_VERSION


def _items():
    return {item["id"]: item for item in read_method_decisions()["items"]}


def test_readonly_registry_has_exact_reproducible_evidence_scope():
    report = read_method_decisions()
    assert report["schema"] == REGISTRY_VERSION
    assert report["evaluated_markets"] == ["SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"]
    assert report["scheduled_contexts"] == 30
    assert report["reconstructed_forecasts"] == 20
    assert report["outcome_evaluable_contexts"] == 17
    assert report["horizon_sessions"] == [5, 10, 20]
    assert report["approved_predictive_methods"] == []
    assert report["strict_historical_as_known_at_T0_proven"] is False
    assert report["prospective_independent_validation_proven"] is False
    assert report["home_installation_not_verified"] is True
    assert report["runtime_legacy_endpoints_not_disabled"] is True


def test_main_and_research_engine_versions_have_distinct_statuses():
    catalog = _items()
    assert catalog["FORECAST_BASELINE_V0_4"]["name"] == ENGINE_VERSION
    assert catalog["FORECAST_BASELINE_V0_4"]["source_state"].startswith("LEGACY_IN_MAIN")
    assert catalog["FORECAST_BASELINE_V0_6"]["source_state"] == "RESEARCH_DRAFT_NOT_IN_MAIN"
    for ident in ("FORECAST_BASELINE_V0_4", "FORECAST_BASELINE_V0_6"):
        row = catalog[ident]
        assert row["state"] == "HISTORICAL_BASELINE_NOT_APPROVED"
        assert row["may_contribute_to_new_directional_model"] is False
        assert row["decision"] == "STOP_FURTHER_DIRECTIONAL_DEVELOPMENT"


def test_candle_volume_proxy_not_allowed_as_directional_evidence():
    catalog = _items()
    row = catalog["CANDLE_VOLUME_PROFILE_PROXY"]
    assert row["state"] == "PROHIBITED_AS_DIRECTIONAL_FEATURE"
    assert row["may_contribute_to_new_directional_model"] is False
    assert "LEGACY_SNAPSHOT" in row["source_state"]


def test_independent_method_set_is_not_claimed_to_have_been_tested_by_v06():
    catalog = _items()
    row = catalog["INDEPENDENT_METHOD_SET_V3"]
    assert row["name"] == METHOD_SET_VERSION
    assert row["state"] == "DIAGNOSTIC_METHODS_UNVALIDATED"
    assert row["validation"] == "NO_INDEPENDENT_METHOD_LEVEL_EVIDENCE_IN_20261016_EXPERIMENT"
    assert set(row["member_methods"]) == {
        "TREND_MOMENTUM", "TIMEFRAME_ALIGNMENT", "HORIZON_MOMENTUM",
        "HORIZON_REVERSION", "REGIME_TREND", "MEAN_REVERSION",
        "VOLATILITY_REGIME", "VOLUME_LEVELS", "FLOW_OI",
    }


def test_market_state_cannot_be_passed_off_as_approved_directional_model():
    catalog = _items()
    for ident in ("MARKET_STATE_SIX_REGIMES_V1", "MARKET_STRUCTURE_FACTS_V1"):
        assert catalog[ident]["source_state"] == "DRAFT_ONLY"
        assert catalog[ident]["may_contribute_to_new_directional_model"] is False
        assert catalog[ident]["state"] != "BASELINE_COMPARATOR"


def test_contextual_indicators_are_not_all_called_failed():
    catalog = _items()
    assert catalog["SMA_RETURN_ER20"]["validation"] == "INDIVIDUAL_DIRECTIONAL_UTILITY_NOT_TESTED"
    assert catalog["SMA_TRUE_RANGE_ATR14"]["decision"] == "KEEP_VERSIONED_FOR_REPRODUCIBILITY_ONLY"


def test_numeric_baseline_metrics_are_unchanged():
    catalog = _items()
    assert catalog["FORECAST_BASELINE_V0_4"]["score_mae_pp_by_sessions"] == {
        "5": 3.7704, "10": 3.3850, "20": 3.6870
    }
    assert catalog["FORECAST_BASELINE_V0_6"]["score_mae_pp_by_sessions"] == {
        "5": 3.7180, "10": 3.2797, "20": 3.3265
    }
    assert catalog["FLAT_PRICE_COMPARATOR"]["score_mae_pp_by_sessions"] == {
        "5": 2.9753, "10": 3.2008, "20": 3.4534
    }


def test_lookups_never_share_mutable_registry_state():
    payload = read_method_decisions()
    payload["items"][0]["state"] = "ACCEPTED"
    payload["items"][0]["evidence"].append("FAKE")
    payload["approved_predictive_methods"].append("FAKE")
    assert read_method_decisions()["approved_predictive_methods"] == []
    assert read_method_decisions("FORECAST_BASELINE_V0_4")["items"][0]["state"] == "HISTORICAL_BASELINE_NOT_APPROVED"
    assert "FAKE" not in read_method_decisions("FORECAST_BASELINE_V0_4")["items"][0]["evidence"]


@pytest.mark.parametrize("key", ["", "DOES_NOT_EXIST", "FORECAST_BASELINE_v0_4", 15, None if False else []])
def test_unknown_or_invalid_single_id_rejected(key):
    with pytest.raises(ValueError):
        read_method_decisions(key)


def test_specific_single_id_returns_one_item_not_a_write():
    report = read_method_decisions("MARKET_STRUCTURE_FACTS_V1")
    assert len(report["items"]) == 1
    assert report["items"][0]["id"] == "MARKET_STRUCTURE_FACTS_V1"
    assert report["records_and_ohlcv_mutated"] is False
