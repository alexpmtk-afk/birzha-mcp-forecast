"""Display naming / migration safety only. No live forecast backtest."""
from __future__ import annotations

from birzha.application.model_catalog import MODEL_CATALOG_VERSION, model_catalog


def test_current_model_numbers_and_legacy_aliases_are_exact():
    result = model_catalog()
    ms = result["models"]
    assert [m["model_number"] for m in ms] == [1, 2, 3, 4]
    assert [m["registry_model_id"] for m in ms] == [
        "M1_GPT_STRUCTURE_CONTROL",
        "M2_TREND_CONSENSUS",
        "M3_REGULARIZED_DIRECTION",
        "M4_MULTICLASS_FIRST_TOUCH",
    ]
    assert [m["legacy_display_number"] for m in ms] == [None, 1, 2, 3]
    assert ms[0]["specification_version"] == "2.2"
    assert ms[0]["lifecycle_status"] == "DESIGN_SPEC"
    assert all(m["lifecycle_status"] == "PAUSED_RESEARCH" for m in ms[1:])
    assert result["catalog_version"] == MODEL_CATALOG_VERSION


def test_catalog_is_read_only_inert_and_never_promotes_legacy_baseline():
    a = model_catalog()
    assert a["approved_new_models"] == []
    assert a["forecast_records_rewritten"] is False
    assert a["deployment_or_activation_performed"] is False
    assert a["legacy_forecast_engine"]["renumbered"] is False
    assert a["legacy_forecast_engine"]["engine_version"] == "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS"
    a["models"][1]["display_name"] = "corrupted"
    b = model_catalog()
    assert b["models"][1]["display_name"] == "Модель 2 — TREND_CONSENSUS"
    assert b["scope"] == "DISPLAY_RENUMBERING_ONLY"


def test_every_number_has_unique_status_and_is_not_legacy_v04_or_v06():
    m = model_catalog()["models"]
    assert len({x["registry_model_id"] for x in m}) == 4
    assert len({x["model_number"] for x in m}) == 4
    assert all("v0.4" not in x["display_name"] for x in m)
    assert all("v0.6" not in x["display_name"] for x in m)
