"""Stable read-only DISPLAY registry for the four forecast research models.

Display renumbering on 2026-10-19 is not an algorithm migration. Forecast
record IDs, legacy engine version, historic evaluations and deployed config
are immutable; no new predictive method is approved or enabled here.
"""
from __future__ import annotations

from copy import deepcopy

MODEL_CATALOG_VERSION = "BIRZHA_FORECAST_DISPLAY_CATALOG_20261019_V1"
GOOGLE_REGISTRY = (
    "https://docs.google.com/spreadsheets/d/"
    "1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit"
)

_MODELS = (
    {
        "model_number": 1,
        "display_name": "Модель 1 — STRUCTURE_CONTROL",
        "registry_model_id": "M1_GPT_STRUCTURE_CONTROL",
        "legacy_display_number": None,
        "legacy_name": None,
        "specification_version": "2.2",
        "lifecycle_status": "DESIGN_SPEC",
        "implementation_status": "NOT_VERIFIED",
        "purpose": "D1 structure, H1/M15 confirmed retest/holding, route and horizon-specific scenarios",
        "reference": "Google «КАРТА ПРОЕКТА — БИРЖА», лист «Модель 1 — инструкция»",
    },
    {
        "model_number": 2,
        "display_name": "Модель 2 — TREND_CONSENSUS",
        "registry_model_id": "M2_TREND_CONSENSUS",
        "legacy_display_number": 1,
        "legacy_name": "TREND_CONSENSUS",
        "specification_version": None,
        "lifecycle_status": "PAUSED_RESEARCH",
        "implementation_status": "PARTIAL_RESEARCH_ONLY",
        "purpose": "Hierarchical trend agreement, state/structure/control/route",
        "reference": "PR #261 research composition (not complete model 2 implementation)",
    },
    {
        "model_number": 3,
        "display_name": "Модель 3 — REGULARIZED_DIRECTION",
        "registry_model_id": "M3_REGULARIZED_DIRECTION",
        "legacy_display_number": 2,
        "legacy_name": "REGULARIZED_DIRECTION",
        "specification_version": None,
        "lifecycle_status": "PAUSED_RESEARCH",
        "implementation_status": "D1_RESEARCH_PROTOTYPE_ONLY",
        "purpose": "Regularized statistical UP/DOWN/ABSTAIN prediction",
        "reference": "Legacy SBER H10 D1 research report; not a production integration",
    },
    {
        "model_number": 4,
        "display_name": "Модель 4 — MULTICLASS_FIRST_TOUCH",
        "registry_model_id": "M4_MULTICLASS_FIRST_TOUCH",
        "legacy_display_number": 3,
        "legacy_name": "MULTICLASS_FIRST_TOUCH",
        "specification_version": None,
        "lifecycle_status": "PAUSED_RESEARCH",
        "implementation_status": "D1_RESEARCH_PROTOTYPE_ONLY",
        "purpose": "UP_FIRST/DOWN_FIRST/NEITHER with explicit AMBIGUOUS outcome",
        "reference": "Legacy SBER H10 D1 research report; not a production integration",
    },
)


def model_catalog() -> dict[str, object]:
    """Read-only display metadata; no source, storage or external requests."""
    return {
        "catalog_version": MODEL_CATALOG_VERSION,
        "source": GOOGLE_REGISTRY,
        "scope": "DISPLAY_RENUMBERING_ONLY",
        "models": deepcopy(list(_MODELS)),
        "legacy_forecast_engine": {
            "engine_version": "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS",
            "status": "SEPARATE_LEGACY_BASELINE",
            "renumbered": False,
        },
        "model_numbers_are_not_probabilities": True,
        "approved_new_models": [],
        "forecast_records_rewritten": False,
        "deployment_or_activation_performed": False,
        "historical_identifiers_preserved": True,
    }
