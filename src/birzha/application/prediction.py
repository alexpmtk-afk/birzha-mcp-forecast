"""Build the versioned Stage D Prediction Contract from a causal MarketSnapshot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from birzha.application.features import _finite_price
from birzha.domain.normalized_features import NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
from birzha.application.snapshot import MarketSnapshotService
from birzha.domain.prediction import (
    PREDICTION_CONTRACT_VERSION,
    PredictionContract,
)
from birzha.domain.snapshot import MarketSnapshot


BASELINE_BARRIER_K = 1.0
DEFAULT_HORIZONS = (5, 10, 20)
FULL_WINDOWS_PREDICTION_VERSION = "PREDICTION_CONTRACT_V2_SMA_TR14_FULL_WINDOWS"


@dataclass(slots=True)
class PredictionContractService:
    snapshots: MarketSnapshotService

    def build(
        self,
        symbol: str,
        *,
        as_of_date: str | None = None,
    ) -> PredictionContract:
        snapshot = self.snapshots.build(symbol, as_of_date=as_of_date)
        return build_prediction_contract(snapshot)


def build_prediction_contract(snapshot: MarketSnapshot) -> PredictionContract:
    p0, coordinate = _reference_price(snapshot)
    if not _finite_price(p0) or p0 == 0:
        raise ValueError("Prediction Contract requires a valid causal P0")

    d1_close = snapshot.d1.last_close
    atr_pct = snapshot.d1.atr_14_pct
    if not _finite_price(d1_close) or d1_close == 0 or not _finite_price(atr_pct) or atr_pct <= 0:
        raise ValueError("Prediction Contract requires causal D1 ATR(14)")

    volatility_price = abs(float(d1_close) * float(atr_pct))
    if not _finite_price(volatility_price) or volatility_price <= 0:
        raise ValueError("causal volatility must be > 0")

    p0 = float(p0)
    up = p0 + BASELINE_BARRIER_K * volatility_price
    down = p0 - BASELINE_BARRIER_K * volatility_price

    if not all(_finite_price(value) for value in (up, down)):
        raise ValueError("causal barriers must be finite")
    full_windows = snapshot.normalized_features is not None and snapshot.normalized_features.version == NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
    contract_version = FULL_WINDOWS_PREDICTION_VERSION if full_windows else PREDICTION_CONTRACT_VERSION
    measure = "D1_SMA_TR14_PRICE_V1" if full_windows else "D1_WILDER_ATR14_PRICE"
    identity = {
        "version": contract_version,
        "symbol": snapshot.symbol,
        "secid": snapshot.secid,
        "t0": snapshot.as_of,
        "p0": round(p0, 10),
        "up": round(up, 10),
        "down": round(down, 10),
        "horizons": list(DEFAULT_HORIZONS),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]

    return PredictionContract(
        contract_id=f"pred_{digest}",
        version=contract_version,
        symbol=snapshot.symbol,
        secid=snapshot.secid,
        t0=snapshot.as_of,
        p0=round(p0, 10),
        price_coordinate=coordinate,
        significant_move_definition="P0 +/- k * causal D1 ATR(14)",
        volatility_measure=measure,
        causal_volatility_price=round(volatility_price, 10),
        barrier_k=BASELINE_BARRIER_K,
        up_barrier=round(up, 10),
        down_barrier=round(down, 10),
        horizons_sessions=DEFAULT_HORIZONS,
        up_hit_rule="price >= up_barrier",
        down_hit_rule="price <= down_barrier",
        ambiguous_path_policy="M15 -> M1 -> TRADES -> AMBIGUOUS_PATH",
        rollover_policy="OUT_OF_SCOPE_ROLLOVER_V0",
    )


def _reference_price(snapshot: MarketSnapshot) -> tuple[float | None, str]:
    if snapshot.m15.last_close is not None:
        return snapshot.m15.last_close, "LAST_COMPLETED_M15_CLOSE"
    if snapshot.h1.last_close is not None:
        return snapshot.h1.last_close, "LAST_COMPLETED_H1_CLOSE"
    return snapshot.d1.last_close, "LAST_COMPLETED_D1_CLOSE"
