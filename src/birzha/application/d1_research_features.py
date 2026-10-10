"""Version-pinned D1-only research features, separate from the Forecast Engine.

A validated session calendar/evidence manifest MUST be supplied by the caller.
This arithmetic cannot attest that past payload versions were available at T0.
No forecast, outcome, model label or calibrated threshold is created here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite

from birzha.domain.market import Candle, CandleSeries

D1_RESEARCH_FEATURE_VERSION = "G2_D1_RESEARCH_SMA_TR14_D20_ER20_W20_V1"
D1_RESEARCH_ATR_METHOD = "SMA_14_TRUE_RANGES_NOT_WILDER"
D1_RESEARCH_ELIGIBILITY = "RECONSTRUCTED_RESEARCH_ONLY"
_VALID_ORIGINS = frozenset({"RECONSTRUCTED_MOEX", "CAPTURED_AT_SYNC"})


@dataclass(frozen=True, slots=True)
class ResearchFeature:
    value: float | None
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class D1ResearchFeatureSet:
    secid: str
    session: str
    evidence_origin: str
    evidence_key: str
    observed_bars: int
    features: tuple[tuple[str, ResearchFeature], ...]
    version: str = D1_RESEARCH_FEATURE_VERSION
    atr_method: str = D1_RESEARCH_ATR_METHOD
    eligibility: str = D1_RESEARCH_ELIGIBILITY
    strict_historical_as_known_at_t0: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.version,
            "secid": self.secid,
            "session": self.session,
            "evidence_origin": self.evidence_origin,
            "evidence_key": self.evidence_key,
            "observed_bars": self.observed_bars,
            "atr_method": self.atr_method,
            "eligibility": self.eligibility,
            "strict_historical_as_known_at_t0": self.strict_historical_as_known_at_t0,
            "features": {name: value.to_dict() for name, value in self.features},
        }


def _numeric(value: float | None) -> bool:
    return value is not None and isinstance(value, (int, float)) and isfinite(value)


def _price_bar_is_valid(candle: Candle) -> bool:
    fields = (candle.open, candle.high, candle.low, candle.close)
    if not all(_numeric(value) and value > 0 for value in fields):
        return False
    assert candle.high is not None and candle.low is not None
    assert candle.open is not None and candle.close is not None
    return candle.low <= min(candle.open, candle.close) <= max(
        candle.open, candle.close
    ) <= candle.high


def _unavailable(reason: str) -> ResearchFeature:
    return ResearchFeature(None, "UNAVAILABLE", reason)


def _needs_window(length: int, required: int) -> ResearchFeature:
    return ResearchFeature(
        None, "INSUFFICIENT_HISTORY", f"requires_{required}_exact_sessions_got_{length}"
    )


def _available(value: float) -> ResearchFeature:
    if not isfinite(value):
        return _unavailable("nonfinite_result")
    return ResearchFeature(value, "AVAILABLE")


def build_d1_research_features(
    series: CandleSeries,
    *,
    exact_secid: str,
    expected_sessions: tuple[str, ...],
    evidence_origin: str,
    evidence_key: str,
) -> D1ResearchFeatureSet:
    """Produce pinned arithmetic for one exact contract; no inferred sessions.

    The caller must independently verify expected_sessions/evidence_key against
    its source calendar and manifest. Passing an arbitrary key does not create
    trusted historical evidence; output is always RECONSTRUCTED_RESEARCH_ONLY.
    """
    if series.timeframe.upper() != "D1":
        raise ValueError("D1-only research feature contract")
    if not exact_secid or exact_secid != series.instrument.secid:
        raise ValueError("exact SECID does not match series")
    if evidence_origin not in _VALID_ORIGINS or not evidence_key:
        raise ValueError("source evidence origin/key required; no implicit provenance")
    if len(series.candles) != len(expected_sessions):
        raise ValueError("session mismatch: missing/extra candles")
    if len(set(expected_sessions)) != len(expected_sessions):
        raise ValueError("duplicate expected sessions")
    try:
        parsed = tuple(date.fromisoformat(s) for s in expected_sessions)
    except ValueError as exc:
        raise ValueError("expected sessions need ISO calendar dates") from exc
    if parsed != tuple(sorted(parsed)):
        raise ValueError("expected sessions must be strictly increasing")
    for candle, session in zip(series.candles, expected_sessions):
        if candle.begin[:10] != session:
            raise ValueError("candle session mismatch: no gap compression")
    if not series.candles:
        raise ValueError("empty D1 session evidence")

    all_bars = series.candles
    last = all_bars[-1]
    n = len(all_bars)
    features: dict[str, ResearchFeature] = {}
    for name, needed in (
        ("atr14_sma_tr", 15),
        ("sma20", 20),
        ("sma50", 50),
        ("d5_atr", 15),
        ("d20_atr", 21),
        ("er20", 21),
        ("w20_atr", 20),
    ):
        if n < needed:
            features[name] = _needs_window(n, needed)

    # Independent OHLC integrity + finality; never compress missing inputs.
    price_valid = all(c.completed and _price_bar_is_valid(c) for c in all_bars)
    if not price_valid:
        for name in (
            "atr14_sma_tr", "sma20", "sma50", "d5_atr",
            "d20_atr", "er20", "w20_atr",
        ):
            if name not in features:
                features[name] = _unavailable("invalid_or_unfinished_exact_session_ohlc")
    else:
        closes = [float(bar.close) for bar in all_bars if bar.close is not None]
        if n >= 20:
            features["sma20"] = _available(sum(closes[-20:]) / 20)
        if n >= 50:
            features["sma50"] = _available(sum(closes[-50:]) / 50)
        atr: float | None = None
        if n >= 15:
            tr: list[float] = []
            for prev, cur in zip(all_bars[-15:-1], all_bars[-14:]):
                assert prev.close is not None and cur.high is not None and cur.low is not None
                tr.append(max(
                    cur.high - cur.low,
                    abs(cur.high - prev.close),
                    abs(cur.low - prev.close),
                ))
            atr = sum(tr) / 14
            features["atr14_sma_tr"] = (
                _available(atr) if atr > 0
                else _unavailable("zero_or_negative_atr14")
            )
        for name, steps in (("d5_atr", 5), ("d20_atr", 20)):
            required = max(steps + 1, 15)
            if n >= required:
                features[name] = (
                    _available((closes[-1] - closes[-steps - 1]) / atr)
                    if atr is not None and atr > 0
                    else _unavailable("atr14_not_positive")
                )
        if n >= 21:
            path = sum(abs(b - a) for a, b in zip(closes[-21:-1], closes[-20:]))
            features["er20"] = (
                _available(abs(closes[-1] - closes[-21]) / path)
                if path > 0
                else _unavailable("zero_20_step_closing_path")
            )
        if n >= 20:
            width = (
                max(float(c.high) for c in all_bars[-20:] if c.high is not None)
                - min(float(c.low) for c in all_bars[-20:] if c.low is not None)
            )
            features["w20_atr"] = (
                _available(width / atr) if atr is not None and atr > 0
                else _unavailable("atr14_not_positive")
            )

    # Volume is not a required D1 price feature. Index volume=0 is not
    # evidence of a broken index price bar and is NOT_APPLICABLE when the
    # declared instrument lacks the VOLUME capability.
    if "VOLUME" not in series.instrument.data_capabilities:
        features["volume_ratio20"] = ResearchFeature(
            None, "NOT_APPLICABLE", "instrument_does_not_declare_VOLUME"
        )
    elif n < 21:
        features["volume_ratio20"] = _needs_window(n, 21)
    else:
        volumes = [bar.volume for bar in all_bars[-21:]]
        if any(not _numeric(x) or x < 0 for x in volumes):
            features["volume_ratio20"] = _unavailable("missing_or_invalid_volume")
        else:
            baseline = sum(float(x) for x in volumes[:-1]) / 20
            features["volume_ratio20"] = (
                _available(float(volumes[-1]) / baseline)
                if baseline > 0
                else _unavailable("zero_volume_baseline")
            )

    # The D1-only price calculator does not silently fabricate flow/profile
    # observations, even if an instrument declares these capabilities.
    for name, capability in (
        ("oi_change_ratio", "OPEN_INTEREST"),
        ("session_vwap", "TRADESTATS"),
        ("profile_poc", "VOLUME"),
    ):
        supported = (
            capability in series.instrument.data_capabilities or
            (capability == "OPEN_INTEREST" and "FUTOI" in series.instrument.data_capabilities)
        )
        features[name] = (
            _unavailable("not_supplied_by_d1_price_only_source")
            if supported else
            ResearchFeature(None, "NOT_APPLICABLE", f"instrument_does_not_declare_{capability}")
        )

    return D1ResearchFeatureSet(
        secid=exact_secid,
        session=expected_sessions[-1],
        evidence_origin=evidence_origin,
        evidence_key=evidence_key,
        observed_bars=n,
        features=tuple(sorted(features.items())),
    )
