"""Read-only method decision registry; never a runtime feature/trading selector.

Maintains decisions and negative evidence independently from preserved Forecast
records, market candles and Forecast/Outcome stores. Does NOT disable existing
legacy server endpoints: switching runtime behaviour needs separate approval.
"""
from __future__ import annotations

from copy import deepcopy

REGISTRY_VERSION = "G2_METHOD_DECISIONS_V1_20261016"
_STATES = frozenset({
    "HISTORICAL_BASELINE_NOT_APPROVED",
    "PROHIBITED_AS_DIRECTIONAL_FEATURE",
    "CONTEXT_ONLY_DIRECTION_NOT_VALIDATED",
    "RESEARCH_CANDIDATE_UNVALIDATED",
    "FACTS_CODE_TESTED_PREDICTION_UNVALIDATED",
    "DIAGNOSTIC_METHODS_UNVALIDATED",
    "BASELINE_COMPARATOR",
})
_ENTRIES = (
    {
        "id": "FORECAST_BASELINE_V0_4",
        "name": "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS",
        "state": "HISTORICAL_BASELINE_NOT_APPROVED",
        "decision": "STOP_FURTHER_DIRECTIONAL_DEVELOPMENT",
        "reason": "In paired reconstructed research, no demonstrated numeric advantage against unchanged-price benchmark on any of three horizons.",
        "validation": "RECONSTRUCTED_2025_2026_17_OUTCOMES_NOT_INDEPENDENT_OOS",
        "source_state": "LEGACY_IN_MAIN_CODE_HOME_INSTALL_NOT_VERIFIED",
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/231"],
        "score_mae_pp_by_sessions": {"5": 3.7704, "10": 3.3850, "20": 3.6870},
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "FORECAST_BASELINE_V0_6",
        "name": "BIRZHA_FORECAST_BASELINE_V0_6_EVIDENCE_DECISION",
        "state": "HISTORICAL_BASELINE_NOT_APPROVED",
        "decision": "STOP_FURTHER_DIRECTIONAL_DEVELOPMENT",
        "reason": "MAE improved vs V0.4 mainly via two additional NEUTRAL predictions; directional superiority unproven; flat-price comparator better at H5/H10.",
        "validation": "RECONSTRUCTED_2025_2026_17_OUTCOMES_NOT_INDEPENDENT_OOS",
        "source_state": "RESEARCH_DRAFT_NOT_IN_MAIN",
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/251",
                     "https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/253"],
        "score_mae_pp_by_sessions": {"5": 3.7180, "10": 3.2797, "20": 3.3265},
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "CANDLE_VOLUME_PROFILE_PROXY",
        "name": "CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1",
        "state": "PROHIBITED_AS_DIRECTIONAL_FEATURE",
        "decision": "DO_NOT_USE_FOR_DIRECTION_CONTROL_ROUTE",
        "reason": "24-bin hourly candle typical-price approximation is not executed-trade price-volume profile. Consultant rejected its use to justify directional evidence.",
        "validation": "METHODOLOGY_REJECTED_PROXY_NOT_INDEPENDENTLY_BENCHMARKED",
        "source_state": "LEGACY_SNAPSHOT_AND_METHOD_SIGNAL_CODE_MAY_STILL_PRODUCE_PROXY",
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/251"],
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "SMA_RETURN_ER20",
        "name": "SMA20/SMA50; return5/10/20; efficiency_ratio20",
        "state": "CONTEXT_ONLY_DIRECTION_NOT_VALIDATED",
        "decision": "RETAIN_AS_DESCRIPTIVE_CONTEXT_ONLY",
        "reason": "No separate indicator ablation/OOS demonstration; existing combined heuristic was not superior. No claim that an individual indicator is intrinsically useless.",
        "validation": "INDIVIDUAL_DIRECTIONAL_UTILITY_NOT_TESTED",
        "source_state": "EXISTING_PRICE_FEATURES",
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/231"],
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "SMA_TRUE_RANGE_ATR14",
        "name": "SMA-TR14 from current legacy feature engine",
        "state": "CONTEXT_ONLY_DIRECTION_NOT_VALIDATED",
        "decision": "KEEP_VERSIONED_FOR_REPRODUCIBILITY_ONLY",
        "reason": "True Range simple average is not Wilder ATR. Old forecast magnitudes remain frozen; use separate feature name/version for any new Wilder computations.",
        "validation": "MAGNITUDE_SCALE_NOT_PREDICTIVE_EDGE",
        "source_state": "LEGACY_IN_MAIN_PRICE_FEATURES",
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/151"],
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "MARKET_STATE_SIX_REGIMES_V1",
        "name": "MARKET_STATE_RULES_RESEARCH_V1",
        "state": "RESEARCH_CANDIDATE_UNVALIDATED",
        "decision": "REQUIRE_INDEPENDENT_SEMANTIC_REVIEW_AND_PROSPECTIVE_TEST",
        "reason": "Six research regime scores are engineering hypotheses with uncalibrated weights and thresholds, not accepted probabilities or proven signals.",
        "validation": "CODE_RESEARCH_ONLY_NO_PREDICTIVE_VALIDATION",
        "source_state": "DRAFT_ONLY",
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/200"],
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "MARKET_STRUCTURE_FACTS_V1",
        "name": "MARKET_STRUCTURE_FACTS_V1_20261016",
        "state": "FACTS_CODE_TESTED_PREDICTION_UNVALIDATED",
        "decision": "PRESERVE_FACTS_AS_RESEARCH_NOT_AS_SIGNAL",
        "reason": "Confirmed two-sided swings HH/HL/LH/LL, Wilder ATR14 and volume/median20 have passed synthetic causal tests, but no real-world predictive edge proven.",
        "validation": "25_TARGETED_517_REPOSITORY_TESTS_PASS_NO_OUTCOME_EXPERIMENT",
        "source_state": "DRAFT_ONLY",
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/254",
                     "https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38949266361"],
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "INDEPENDENT_METHOD_SET_V3",
        "name": "BIRZHA_METHOD_SET_V3",
        "state": "DIAGNOSTIC_METHODS_UNVALIDATED",
        "decision": "RESEARCH_EACH_METHOD_SEPARATELY_BEFORE_ANY_ADOPTION",
        "reason": "Existing diagnostic method signals are not assessed by V0.4/V0.6 paired 17-case experiment; in particular VOLUME_LEVELS may consume candle-based proxy.",
        "validation": "NO_INDEPENDENT_METHOD_LEVEL_EVIDENCE_IN_20261016_EXPERIMENT",
        "source_state": "DIAGNOSTIC_CODE_IN_MAIN",
        "member_methods": [
            "TREND_MOMENTUM", "TIMEFRAME_ALIGNMENT", "HORIZON_MOMENTUM",
            "HORIZON_REVERSION", "REGIME_TREND", "MEAN_REVERSION",
            "VOLATILITY_REGIME", "VOLUME_LEVELS", "FLOW_OI",
        ],
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/231"],
        "may_contribute_to_new_directional_model": False,
    },
    {
        "id": "FLAT_PRICE_COMPARATOR",
        "name": "unchanged-price benchmark (zero return)",
        "state": "BASELINE_COMPARATOR",
        "decision": "RETAIN_ONLY_AS_COMPARATOR_NOT_MODEL",
        "reason": "Required paired magnitude baseline for each eligible horizon. Cannot imply that flat price is a predictive or executable trading method.",
        "validation": "RECONSTRUCTED_2025_2026_17_OUTCOMES",
        "source_state": "RESEARCH_EVALUATION_ONLY",
        "score_mae_pp_by_sessions": {"5": 2.9753, "10": 3.2008, "20": 3.4534},
        "evidence": ["https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/231"],
        "may_contribute_to_new_directional_model": False,
    },
)


def read_method_decisions(method_id: str | None = None) -> dict[str, object]:
    """No file/db/network writes, no dynamic engine switching or model approval."""
    if method_id is not None and (not isinstance(method_id, str) or not method_id):
        raise ValueError("nonempty method_id required")
    entries = {row["id"]: row for row in _ENTRIES}
    if len(entries) != len(_ENTRIES) or any(
        row["state"] not in _STATES for row in _ENTRIES
    ):
        raise RuntimeError("invalid method decision registry")
    if method_id is not None:
        if method_id not in entries:
            raise ValueError("unknown method_id")
        selected = [entries[method_id]]
    else:
        selected = list(_ENTRIES)
    return deepcopy({
        "schema": REGISTRY_VERSION,
        "decision_date": "2026-10-16",
        "scope": "METHOD_STATUS_AND_RESEARCH_EVIDENCE_ONLY",
        "canonical_source": "src/birzha/application/method_registry.py",
        "evaluated_markets": ["SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"],
        "scheduled_contexts": 30,
        "reconstructed_forecasts": 20,
        "outcome_evaluable_contexts": 17,
        "horizon_sessions": [5, 10, 20],
        "strict_historical_as_known_at_T0_proven": False,
        "prospective_independent_validation_proven": False,
        "approved_predictive_methods": [],
        "runtime_legacy_endpoints_not_disabled": True,
        "home_installation_not_verified": True,
        "records_and_ohlcv_mutated": False,
        "items": selected,
    })
