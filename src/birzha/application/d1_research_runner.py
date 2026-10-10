"""In-memory, all-or-nothing descriptive research evaluation; no artifact writes."""
from __future__ import annotations

import json

from birzha.application.d1_research_dataset_adapter import (
    ADAPTER_VERSION, MARKETS, canonical_json, validate_d1_research_dataset,
)
from birzha.application.d1_research_features import (
    D1_RESEARCH_ATR_METHOD, D1_RESEARCH_FEATURE_VERSION,
)
from birzha.application.d1_research_regime import (
    REGIME_VERSION, RegimeParameters, classify_d1_research_regime,
)

RUN_VERSION = "G2_D1_DESCRIPTIVE_RESEARCH_RUN_V1"


def run_d1_descriptive_research(
    manifest_bytes: bytes, accepted_bytes: bytes, exclusion_bytes: bytes, *,
    trusted_manifest_sha256: str, parameters: RegimeParameters,
) -> bytes:
    """Return one complete canonical JSON payload, or raise without output.

    Parameters have no default. This function does not optimize or calibrate.
    A real-data parameter application requires a separately agreed experiment.
    """
    if not isinstance(parameters, RegimeParameters):
        raise ValueError("explicit validated RegimeParameters required")
    dataset = validate_d1_research_dataset(
        manifest_bytes, accepted_bytes, exclusion_bytes,
        trusted_manifest_sha256=trusted_manifest_sha256,
    )
    manifest = json.loads(dataset.manifest_json)
    summary = {
        market: {
            "accepted_inputs": manifest["per_market"][market]["admitted_reconstructed"],
            "upstream_excluded": manifest["per_market"][market]["excluded"],
            "upstream_exclusion_reasons": manifest["per_market"][market]["exclusion_reasons"],
            "invalid_inputs": 0,
            "TREND_UP": 0, "TREND_DOWN": 0, "BALANCE": 0, "UNKNOWN_TRANSITION": 0,
        }
        for market in MARKETS
    }
    results = []
    for row in dataset.rows:
        result = classify_d1_research_regime(row.features, parameters=parameters)
        if result.state == "UNKNOWN":
            if result.reasons != ("OUTSIDE_DECLARED_REGIME_HYPOTHESES",):
                raise ValueError("validated input rejected by classifier; whole run aborted")
            key = "UNKNOWN_TRANSITION"
        elif result.state == "TREND":
            key = "TREND_" + result.direction
        else:
            key = "BALANCE"
        summary[row.market][key] += 1
        results.append({"input": json.loads(row.original_json), "regime": result.to_dict()})
    payload = {
        "schema": RUN_VERSION, "completion": "COMPLETE",
        "scope": "RECONSTRUCTED_RESEARCH_ONLY",
        "strict_historical_as_known_at_T0": False,
        "adapter_version": ADAPTER_VERSION, "regime_version": REGIME_VERSION,
        "feature_version": D1_RESEARCH_FEATURE_VERSION, "atr_method": D1_RESEARCH_ATR_METHOD,
        "trusted_manifest_sha256": dataset.manifest_sha256,
        "input_manifest": manifest,
        "parameters": parameters.to_dict(), "parameter_fingerprint": parameters.fingerprint,
        "parameter_status": "UNCALIBRATED_HYPOTHESIS",
        "results": results,
        "upstream_exclusions": [json.loads(row) for row in dataset.exclusions_json],
        "per_market": summary,
        "accepted_result_count": len(results),
        "upstream_exclusion_count": len(dataset.exclusions_json),
    }
    return (canonical_json(payload) + "\n").encode("utf-8")
