"""Single read-only Forecast research composition root (not operational ForecastService).

Combines independently pinned *existing* provisional research producers from
Draft #205 (Market State, Control/Holding, Level/Route, Alignment15) and the
descriptive Market Structure V2 from Draft #259. Compares unfitted methods
using PR #260 factor fusion. Never fetches market data, issues predictions,
creates journals, or presents a hypothetical direction as an approved forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from birzha.application.control_holding import control_holding_from_snapshot
from birzha.application.g2_alignment15_proposal import propose_alignment_and_no_trade
from birzha.application.g2_factor_fusion_research import (
    FactorEvidence, VERSION as FUSION_VERSION, compare_factor_combinations,
)
from birzha.application.level_route import assess_levels_and_route
from birzha.application.market_state_classifier import classify_market_state_from_snapshot
from birzha.application.market_structure_facts import (
    VERSION as STRUCTURE_VERSION, build_market_structure_facts,
)
from birzha.domain.market import CandleSeries, Instrument
from birzha.domain.snapshot import MarketSnapshot, market_snapshot_id

VERSION = "G2_UNIFIED_FORECAST_RESEARCH_COMPOSITION_V1"
SOURCE_BRANCH = "chatgpt/g2-level-first-observable-research-20261011"
SOURCE_LEVEL_PR = 205
SOURCE_STRUCTURE_PR = 259
FATAL_CONTROL_EVIDENCE = frozenset({
    "SNAPSHOT_TIME_INVALID", "INSTRUMENT_OR_TIMEFRAME_MISMATCH",
    "DATA_QUALITY_NOT_PASS", "M15_QUALITY_CONTRACT_NOT_PASS",
    "M15_COMPLETE_HISTORY_MISMATCH", "NONCAUSAL_OR_UNVERIFIED_M15_CANDLE",
    "SNAPSHOT_LAST_CLOSE_DIFFERS_FROM_SOURCE", "CANDLES_CAPABILITY_MISSING",
    "ATR_SCALE_UNAVAILABLE",
})
REGISTERED_VARIANTS = (
    {"id": "LEGACY_V04", "role": "SEPARATE_UNVALIDATED_BENCHMARK", "called": False},
    {"id": "LEGACY_V06", "role": "SEPARATE_UNVALIDATED_BENCHMARK", "called": False},
    {"id": "ALIGNMENT15_V2", "role": "SEPARATE_UNAPPROVED_PROPOSAL", "called": True},
    {"id": "GROUP_MAJORITY", "role": "RESEARCH_CANDIDATE", "called": True},
    {"id": "GROUP_MEAN", "role": "RESEARCH_CANDIDATE", "called": True},
    {"id": "STATE_CONTROL_ROUTE_CONSENSUS", "role": "RESEARCH_CANDIDATE", "called": True},
    {"id": "MARKET_STRUCTURE_V2", "role": "DESCRIPTIVE_INPUT_NOT_A_PREDICTOR", "called": False},
)


def _numeric(value: object, *, positive: bool = False) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else True)


def _factor(family: str, *, status: str, signed: float | None = None) -> FactorEvidence:
    # No original provider SHA or first-received clock is manufactured here.
    # The verified research reports carry the snapshot content identity.
    return FactorEvidence(family=family, status=status, signed_strength=signed)


def _checked_identity(snapshot: MarketSnapshot, instrument: Instrument, series: CandleSeries) -> None:
    if not isinstance(snapshot, MarketSnapshot) or not isinstance(instrument, Instrument) or not isinstance(series, CandleSeries):
        raise TypeError("MarketSnapshot, Instrument and original M15 CandleSeries required")
    if series.timeframe != "M15" or not all([
        snapshot.symbol == instrument.symbol == series.instrument.symbol,
        snapshot.secid == instrument.secid == series.instrument.secid,
        instrument.board == series.instrument.board,
        instrument.engine == series.instrument.engine,
        instrument.market == series.instrument.market,
    ]):
        raise ValueError("exact SECID/instrument/board/M15 source mismatch")
    if not snapshot.secid or not snapshot.as_of:
        raise ValueError("snapshot identity or cutoff missing")


def _optional_structure(
    snapshot: MarketSnapshot, instrument: Instrument, series: CandleSeries | None, timeframe: str,
) -> dict[str, Any]:
    if series is None:
        return {"status": "NOT_PROVIDED", "timeframe": timeframe,
                "schema": STRUCTURE_VERSION, "predictor": False}
    if not isinstance(series, CandleSeries) or series.timeframe != timeframe or (
        series.instrument.secid != instrument.secid or
        series.instrument.symbol != instrument.symbol or
        series.instrument.board != instrument.board or
        series.instrument.engine != instrument.engine or
        series.instrument.market != instrument.market
    ):
        raise ValueError("descriptive structure requires the same exact contract and timeframe")
    # Do not silently downgrade failed strict source checks to reconstructed.
    facts = build_market_structure_facts(series, t0=snapshot.as_of, mode="RECONSTRUCTED_RESEARCH_ONLY")
    return {
        "status": "DESCRIPTIVE_ONLY_NOT_TIME_ATTESTED",
        "timeframe": timeframe,
        "schema": STRUCTURE_VERSION,
        "predictor": False,
        "result": facts,
    }


@dataclass(frozen=True, slots=True)
class ForecastResearchService:
    """Opt-in orchestration of installed research engines on *caller-supplied* data."""

    def build(
        self, snapshot: MarketSnapshot, instrument: Instrument, m15_series: CandleSeries,
        *, d1_series: CandleSeries | None = None, h1_series: CandleSeries | None = None,
    ) -> dict[str, Any]:
        _checked_identity(snapshot, instrument, m15_series)
        original = market_snapshot_id(snapshot)
        state = classify_market_state_from_snapshot(snapshot, instrument)
        control = control_holding_from_snapshot(snapshot, instrument, m15_series)
        route = assess_levels_and_route(snapshot, instrument, m15_series)
        alignment = propose_alignment_and_no_trade(snapshot, instrument, m15_series)

        for name, obj in (("market_state", state), ("control", control),
                          ("route", route), ("alignment", alignment)):
            if (obj.get("snapshot_id") != original or obj.get("secid") != snapshot.secid
                    or obj.get("as_of") != snapshot.as_of):
                raise ValueError(f"{name} returned unrelated / changed input evidence")

        fatal = sorted(FATAL_CONTROL_EVIDENCE.intersection(control.get("reason_codes", ())))
        source_quality_ok = snapshot.data_quality == "PASS" and not fatal
        regime = state.get("candidate_regime")
        state_sign = (
            1.0 if regime == "TREND_UP" else -1.0 if regime == "TREND_DOWN" else None
        ) if state.get("classification_status") == "PROVISIONAL_CANDIDATE" else None
        control_side = control.get("control") if control.get("classification_status") == "PROVISIONAL_HOLDING_CANDIDATE" else None
        control_sign = 1.0 if control_side == "BUYERS" else -1.0 if control_side == "SELLERS" else None

        pressure = control["pressure_separate_from_control"]
        delta = pressure.get("delta_ratio")
        delta_ok = pressure.get("status") == "AVAILABLE" and _numeric(delta) and -1 <= delta <= 1
        route_sign = None
        if (route.get("classification_status") == "PROVISIONAL_ROUTE_CANDIDATE"
                and route.get("route_confidence_research_only") in ("HIGH", "MEDIUM")):
            route_sign = 1.0 if route.get("route_direction") == "UP" else (
                -1.0 if route.get("route_direction") == "DOWN" else None
            )

        eff = snapshot.normalized_features
        efficiencies = (
            (eff.d1_efficiency_20, eff.h1_efficiency_20, eff.m15_efficiency_20)
            if eff is not None else ()
        )
        efficiency_ok = len(efficiencies) == 3 and all(
            _numeric(value) and 0 <= value <= 1 for value in efficiencies
        )
        effort = control.get("candle_activity")
        effort_ok = isinstance(effort, dict) and _numeric(
            effort.get("reaction_volume_over_previous_20_median"), positive=True,
        )
        location_ok = alignment.get("location_exact_profile_candidate") in (
            "ABOVE_VAH", "BELOW_VAL", "INSIDE_VALUE", "AT_POC"
        )
        atr = eff.d1_atr_price_scale if eff is not None else None
        volatility_ok = source_quality_ok and _numeric(atr, positive=True)
        # An unavailable source is NOT a neutral vote. Volume/efficiency/
        # location/volatility are context, not invented directional signals.
        evidence = (
            _factor("market_state", status="AVAILABLE" if state_sign is not None else "UNAVAILABLE", signed=state_sign),
            _factor("control", status="AVAILABLE" if control_sign is not None else "UNAVAILABLE", signed=control_sign),
            _factor("pressure", status="AVAILABLE" if delta_ok else "UNAVAILABLE", signed=float(delta) if delta_ok else None),
            _factor("effort_result", status="AVAILABLE" if effort_ok else "UNAVAILABLE"),
            _factor("route", status="AVAILABLE" if route_sign is not None else "UNAVAILABLE", signed=route_sign),
            _factor("location", status="AVAILABLE" if location_ok else "PROXY" if snapshot.volume_profile is not None else "UNAVAILABLE"),
            _factor("efficiency", status="AVAILABLE" if efficiency_ok else "UNAVAILABLE"),
            _factor("volatility", status="AVAILABLE" if volatility_ok else "DEGRADED"),
        )
        fusion = compare_factor_combinations(
            as_of=snapshot.as_of, evidence=evidence, mode="RECONSTRUCTED_RESEARCH_ONLY",
        )

        # Protocol08 forbids a directional route during BALANCE; never let
        # a group-majority candidate override that market-state fact.
        no_direction_regime = regime in ("BALANCE", "EVENT")
        if not source_quality_ok or no_direction_regime:
            reason = "SOURCE_EVIDENCE_UNSAFE" if not source_quality_ok else "BALANCE_OR_EVENT_NO_DIRECTIONAL_ROUTE"
            fusion["models"] = {
                k: {**v, "candidate_direction": "ABSTAIN", "reason": reason}
                for k, v in fusion["models"].items()
            }

        # Optional Market Structure V2 contributes only descriptive facts.
        # No duplicate voting against Market State; no silent T0 admission.
        structures = {
            "D1": _optional_structure(snapshot, instrument, d1_series, "D1"),
            "H1": _optional_structure(snapshot, instrument, h1_series, "H1"),
        }
        payload = json.dumps(
            {"snapshot_id": original, "fusion_hash": fusion["input_sha256"],
             "d1_sha": structures["D1"].get("result", {}).get("data_sha256"),
             "h1_sha": structures["H1"].get("result", {}).get("data_sha256")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        composition_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return {
            "schema": VERSION,
            "symbol": snapshot.symbol, "secid": snapshot.secid, "as_of": snapshot.as_of,
            "snapshot_id": original,
            "composition_sha256": composition_sha256,
            "evidence_mode": "RECONSTRUCTED_RESEARCH_ONLY",
            "original_provider_first_receipt_attested": False,
            "frozen_research_producers": {
                "market_state": state["schema"],
                "control_holding": control["schema"],
                "level_route": route["schema"],
                "alignment15": alignment["schema"],
                "market_structure": STRUCTURE_VERSION,
                "factor_fusion": FUSION_VERSION,
            },
            "market_state": state, "control_holding": control,
            "level_route": route, "alignment15": alignment,
            "market_structure_facts": structures,
            "factor_evidence": [
                {"family": x.family, "status": x.status, "signed_strength": x.signed_strength,
                 "meaning": "RESEARCH_CATEGORICAL_DIRECTION_NOT_PROBABILITY" if x.signed_strength is not None
                 else "UNSIGNED_CONTEXT_OR_MISSING"}
                for x in evidence
            ],
            "candidate_comparisons": fusion,
            "benchmark_registry": [dict(x) for x in REGISTERED_VARIANTS],
            "fatal_source_evidence": fatal,
            "operational_forecast": None,
            "selected_approved_predictive_model": None,
            "outcomes": None,
            "model_performance": "NOT_EVALUATED_PENDING_CONSULTANT_REVIEW",
            "trade_or_execution_authorized": False,
            "existing_forecast_or_outcome_modified": False,
            "production_or_home_activated": False,
        }
