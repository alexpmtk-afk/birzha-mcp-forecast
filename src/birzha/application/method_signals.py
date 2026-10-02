"""Independent forecast-method diagnostics for causal market snapshots.

These signals deliberately do not replace the production baseline forecast yet.
They expose separable evidence blocks so each method can be backtested and
accepted or rejected independently before any ensemble/model-selection step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from birzha.domain.snapshot import MarketSnapshot, TimeframeState


METHOD_SET_VERSION = "BIRZHA_METHOD_SET_V3"


@dataclass(frozen=True, slots=True)
class ForecastMethodSignal:
    name: str
    role: str
    available: bool
    score: float | None
    direction: str
    strength: float | None
    evidence: tuple[str, ...]
    context_state: str | None = None
    horizon_scores: tuple[tuple[int, float], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_method_signals(snapshot: MarketSnapshot) -> tuple[ForecastMethodSignal, ...]:
    """Return independent, non-probabilistic method signals for one T0 snapshot."""

    return (
        _trend_momentum(snapshot),
        _timeframe_alignment(snapshot),
        _horizon_momentum(snapshot),
        _horizon_reversion(snapshot),
        _regime_trend(snapshot),
        _mean_reversion(snapshot),
        _volatility_regime(snapshot),
        _volume_levels(snapshot),
        _flow_oi(snapshot),
    )


def _direction(score: float, threshold: float = 0.15) -> str:
    if score >= threshold:
        return "UP"
    if score <= -threshold:
        return "DOWN"
    return "NEUTRAL"


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _directional_signal(name: str, score: float, evidence: list[str]) -> ForecastMethodSignal:
    normalized = round(_clamp(score), 6)
    return ForecastMethodSignal(
        name=name,
        role="DIRECTIONAL",
        available=True,
        score=normalized,
        direction=_direction(normalized),
        strength=round(abs(normalized), 6),
        evidence=tuple(evidence),
    )


def _trend_momentum(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    weighted = (
        0.55 * snapshot.d1.trend_score
        + 0.30 * snapshot.h1.trend_score
        + 0.15 * snapshot.m15.trend_score
    )
    # TimeframeFeatureEngine trend_score is normally bounded close to +/-4.5.
    score = weighted / 4.5
    evidence = [
        f"D1:trend_score={snapshot.d1.trend_score:.6f}",
        f"H1:trend_score={snapshot.h1.trend_score:.6f}",
        f"M15:trend_score={snapshot.m15.trend_score:.6f}",
        f"D1:return20={_fmt(snapshot.d1.return_20)}",
        f"H1:return20={_fmt(snapshot.h1.return_20)}",
        f"M15:return20={_fmt(snapshot.m15.return_20)}",
    ]
    return _directional_signal("TREND_MOMENTUM", score, evidence)


def _timeframe_alignment(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    votes: list[int] = []
    evidence: list[str] = []
    for state in (snapshot.d1, snapshot.h1, snapshot.m15):
        local_votes: list[int] = []
        for label, value in (("r5", state.return_5), ("r20", state.return_20)):
            if value is None:
                continue
            vote = 1 if value > 0 else -1 if value < 0 else 0
            votes.append(vote)
            local_votes.append(vote)
            evidence.append(f"{state.timeframe}:{label}={value:.6f},vote={vote:+d}")
        if not local_votes:
            evidence.append(f"{state.timeframe}:no_return_votes")
    if not votes:
        return ForecastMethodSignal(
            name="TIMEFRAME_ALIGNMENT",
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence),
        )
    return _directional_signal("TIMEFRAME_ALIGNMENT", sum(votes) / len(votes), evidence)




def _horizon_directional_signal(
    name: str,
    scores: dict[int, float],
    evidence: list[str],
) -> ForecastMethodSignal:
    usable = {
        int(sessions): round(_clamp(float(score)), 6)
        for sessions, score in scores.items()
    }
    if not usable:
        return ForecastMethodSignal(
            name=name,
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence or ["no_horizon_scores"]),
        )
    average = sum(usable.values()) / len(usable)
    return ForecastMethodSignal(
        name=name,
        role="DIRECTIONAL",
        available=True,
        score=round(_clamp(average), 6),
        direction=_direction(average),
        strength=round(sum(abs(v) for v in usable.values()) / len(usable), 6),
        evidence=tuple(evidence),
        horizon_scores=tuple(sorted(usable.items())),
    )


def _horizon_return_scores(snapshot: MarketSnapshot) -> tuple[dict[int, float], list[str]]:
    scales = {5: 0.02, 10: 0.03, 20: 0.05}
    values = {
        5: snapshot.d1.return_5,
        10: snapshot.d1.return_10,
        20: snapshot.d1.return_20,
    }
    scores: dict[int, float] = {}
    evidence: list[str] = []
    for sessions in (5, 10, 20):
        value = values[sessions]
        evidence.append(f"D1:return{sessions}={_fmt(value)}")
        if value is None:
            continue
        scores[sessions] = _clamp(value / scales[sessions])
    return scores, evidence


def _horizon_momentum(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    scores, evidence = _horizon_return_scores(snapshot)
    return _horizon_directional_signal(
        "HORIZON_MOMENTUM",
        scores,
        evidence + ["rule=continue_same_horizon_D1_move"],
    )


def _horizon_reversion(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    scores, evidence = _horizon_return_scores(snapshot)
    reversed_scores = {sessions: -score for sessions, score in scores.items()}
    return _horizon_directional_signal(
        "HORIZON_REVERSION",
        reversed_scores,
        evidence + ["rule=reverse_same_horizon_D1_move"],
    )

def _regime_trend(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    """Follow trend only when D1 price action is demonstrably efficient."""

    er = snapshot.d1.efficiency_ratio_20
    evidence = [
        f"D1:er20={_fmt(er)}",
        f"D1:trend_score={snapshot.d1.trend_score:.6f}",
        f"H1:trend_score={snapshot.h1.trend_score:.6f}",
        f"M15:trend_score={snapshot.m15.trend_score:.6f}",
    ]
    if er is None or er < 0.45:
        return ForecastMethodSignal(
            name="REGIME_TREND",
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence + ["regime_not_efficient_trend"]),
        )

    weighted = (
        0.60 * snapshot.d1.trend_score
        + 0.25 * snapshot.h1.trend_score
        + 0.15 * snapshot.m15.trend_score
    )
    regime_strength = _clamp((er - 0.35) / 0.40, 0.25, 1.0)
    score = (weighted / 4.5) * regime_strength
    evidence.append(f"regime_strength={regime_strength:.6f}")
    return _directional_signal("REGIME_TREND", score, evidence)


def _mean_reversion(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    """Fade stretched price only in low-efficiency/choppy D1 regimes."""

    er = snapshot.d1.efficiency_ratio_20
    evidence = [f"D1:er20={_fmt(er)}"]
    if er is None or er >= 0.30:
        return ForecastMethodSignal(
            name="MEAN_REVERSION",
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence + ["regime_not_mean_reverting"]),
        )

    weighted_components: list[tuple[float, float]] = []
    for state, weight in (
        (snapshot.d1, 0.55),
        (snapshot.h1, 0.30),
        (snapshot.m15, 0.15),
    ):
        local: list[float] = []
        if state.last_close is not None and state.vwap_20 not in {None, 0.0}:
            vwap_stretch = _clamp((state.last_close / state.vwap_20 - 1.0) / 0.02)
            local.append(vwap_stretch)
            evidence.append(
                f"{state.timeframe}:vwap_stretch={vwap_stretch:.6f}"
            )
        if state.price_location_20 is not None:
            location_stretch = _clamp((state.price_location_20 - 0.5) * 2.0)
            local.append(location_stretch)
            evidence.append(
                f"{state.timeframe}:location_stretch={location_stretch:.6f}"
            )
        if state.return_5 is not None:
            recent_stretch = _clamp(state.return_5 / 0.03)
            local.append(recent_stretch)
            evidence.append(
                f"{state.timeframe}:return5_stretch={recent_stretch:.6f}"
            )
        if local:
            weighted_components.append((sum(local) / len(local), weight))

    if not weighted_components:
        return ForecastMethodSignal(
            name="MEAN_REVERSION",
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence + ["no_stretch_features"]),
        )

    total_weight = sum(weight for _, weight in weighted_components)
    stretch = sum(value * weight for value, weight in weighted_components) / total_weight
    # High/positive stretch predicts DOWN; low/negative stretch predicts UP.
    chop_strength = _clamp((0.35 - er) / 0.25, 0.40, 1.0)
    score = -stretch * chop_strength
    evidence.extend(
        [
            f"combined_stretch={stretch:.6f}",
            f"chop_strength={chop_strength:.6f}",
        ]
    )
    return _directional_signal("MEAN_REVERSION", score, evidence)

def _volatility_regime(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    atr = snapshot.d1.atr_14_pct
    er = snapshot.d1.efficiency_ratio_20
    evidence = [f"D1:atr14_pct={_fmt(atr)}", f"D1:er20={_fmt(er)}"]
    if atr is None and er is None:
        return ForecastMethodSignal(
            name="VOLATILITY_REGIME",
            role="CONTEXT",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence),
            context_state="UNKNOWN",
        )

    states: list[str] = []
    if atr is not None:
        if atr >= 0.025:
            states.append("HIGH_VOL")
        elif atr <= 0.008:
            states.append("LOW_VOL")
        else:
            states.append("NORMAL_VOL")
    if er is not None:
        if er < 0.20:
            states.append("CHOPPY")
        elif er >= 0.45:
            states.append("EFFICIENT_TREND")
        else:
            states.append("MIXED_EFFICIENCY")

    return ForecastMethodSignal(
        name="VOLATILITY_REGIME",
        role="CONTEXT",
        available=True,
        score=None,
        direction="CONTEXT",
        strength=None,
        evidence=tuple(evidence),
        context_state="+".join(states),
    )


def _volume_levels(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    components: list[float] = []
    evidence: list[str] = []

    for state in (snapshot.d1, snapshot.h1, snapshot.m15):
        local: list[float] = []
        if state.last_close is not None and state.vwap_20 not in {None, 0.0}:
            vwap_vote = _clamp((state.last_close / state.vwap_20 - 1.0) / 0.02)
            local.append(vwap_vote)
            evidence.append(
                f"{state.timeframe}:close={state.last_close:.6f},vwap20={state.vwap_20:.6f},vwap_score={vwap_vote:.6f}"
            )
        if state.price_location_20 is not None:
            location_score = _clamp((state.price_location_20 - 0.5) * 2.0)
            local.append(location_score)
            evidence.append(
                f"{state.timeframe}:price_location20={state.price_location_20:.6f},location_score={location_score:.6f}"
            )
        if local:
            local_score = sum(local) / len(local)
            if state.volume_ratio_20 is not None:
                # Volume confirms location but never flips its sign.
                confirmation = min(1.25, max(0.75, state.volume_ratio_20))
                local_score *= confirmation
                evidence.append(
                    f"{state.timeframe}:volume_ratio20={state.volume_ratio_20:.6f},confirmation={confirmation:.6f}"
                )
            components.append(_clamp(local_score))

    profile = snapshot.volume_profile
    if profile is not None and snapshot.h1.last_close is not None:
        price = snapshot.h1.last_close
        if price > profile.vah:
            profile_score = 1.0
        elif price < profile.val:
            profile_score = -1.0
        elif price > profile.poc:
            profile_score = 0.25
        elif price < profile.poc:
            profile_score = -0.25
        else:
            profile_score = 0.0
        components.append(profile_score)
        evidence.append(
            f"PROFILE:price={price:.6f},poc={profile.poc:.6f},vah={profile.vah:.6f},val={profile.val:.6f},score={profile_score:.6f}"
        )

    if not components:
        return ForecastMethodSignal(
            name="VOLUME_LEVELS",
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence or ["no_vwap_location_or_profile"]),
        )

    return _directional_signal("VOLUME_LEVELS", sum(components) / len(components), evidence)


def _flow_oi(snapshot: MarketSnapshot) -> ForecastMethodSignal:
    flow = snapshot.flow
    if flow is None:
        return ForecastMethodSignal(
            name="FLOW_OI",
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=("flow_not_available",),
        )

    components: list[float] = []
    evidence: list[str] = []

    if flow.volume_delta_ratio is not None:
        delta_score = _clamp(flow.volume_delta_ratio / 0.30)
        components.append(delta_score)
        evidence.append(f"delta_ratio={flow.volume_delta_ratio:.6f},delta_score={delta_score:.6f}")

    price = flow.price_change_pct
    oi_change = flow.algopack_oi_change
    if price not in {None, 0.0} and oi_change not in {None, 0.0}:
        price_sign = 1.0 if price > 0 else -1.0
        # Rising OI confirms the price direction; falling OI weakens it.
        oi_score = price_sign if oi_change > 0 else -0.35 * price_sign
        components.append(oi_score)
        evidence.append(
            f"price_change_pct={price:.6f},oi_change={oi_change:.6f},oi_score={oi_score:.6f}"
        )

    if flow.individuals is not None and flow.legal_entities is not None:
        fiz = flow.individuals.net_position
        yur = flow.legal_entities.net_position
        if fiz is not None and yur is not None and fiz != yur:
            positioning = 0.20 if fiz > yur else -0.20
            components.append(positioning)
            evidence.append(
                f"futoi_individuals_net={fiz:.6f},legal_net={yur:.6f},positioning_score={positioning:.6f}"
            )

    if not components:
        return ForecastMethodSignal(
            name="FLOW_OI",
            role="DIRECTIONAL",
            available=False,
            score=None,
            direction="UNAVAILABLE",
            strength=None,
            evidence=tuple(evidence or ["flow_present_but_no_directional_fields"]),
        )

    return _directional_signal("FLOW_OI", sum(components) / len(components), evidence)


def _fmt(value: float | None) -> str:
    return "NULL" if value is None else f"{value:.6f}"
