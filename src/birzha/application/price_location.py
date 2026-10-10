"""Exact price/level comparisons, separate from the unaccepted LOCATION labels."""
import hashlib
from birzha.application.features import _finite_price
from birzha.application.price_levels import build_price_level_evidence, _quality
from birzha.application.level_reaction import canonical_bytes
from birzha.domain.snapshot import market_snapshot_id

LOCATION_FACTS_VERSION = "PRICE_LOCATION_FACTS_V1"


def _finite_result(value):
    return value if _finite_price(value) else None


def _reference(snapshot):
    # Match the existing M15/H1/D1 selection; never silently replace a bad choice.
    for tf in ("M15", "H1", "D1"):
        state = getattr(snapshot, tf.lower())
        if state.last_close is not None:
            status, reason, end = _quality(snapshot, tf, 1)
            if status == "AVAILABLE" and not _finite_price(state.last_close):
                status, reason = "UNAVAILABLE", "REFERENCE_PRICE_INVALID"
            normalized = snapshot.normalized_features
            if status == "AVAILABLE" and (normalized is None or not _finite_price(normalized.current_price) or normalized.current_price != round(state.last_close, 6)):
                status, reason = "UNAVAILABLE", "NORMALIZED_REFERENCE_CONFLICT"
            return {"status": status, "reason": reason, "timeframe": tf,
                    "source_end": end, "price": state.last_close if status == "AVAILABLE" else None}
    return {"status": "UNAVAILABLE", "reason": "REFERENCE_PRICE_MISSING", "timeframe": None, "source_end": None, "price": None}


def _atr(snapshot):
    status, reason, end = _quality(snapshot, "D1", 15)
    state = snapshot.d1
    scale = None
    if status == "AVAILABLE":
        if not _finite_price(state.last_close) or not _finite_price(state.atr_14_pct) or state.atr_14_pct <= 0:
            reason = "ATR_SCALE_UNAVAILABLE"
        else:
            candidate = _finite_result(abs(state.last_close) * state.atr_14_pct)
            scale = round(candidate, 6) if candidate is not None else None
            if scale is None or scale <= 0:
                reason = "ATR_SCALE_UNAVAILABLE"
            elif not _finite_price(snapshot.normalized_features.d1_atr_price_scale) or snapshot.normalized_features.d1_atr_price_scale != scale:
                reason = "NORMALIZED_ATR_CONFLICT"
    return {"status": "AVAILABLE" if reason is None and scale is not None else "UNAVAILABLE", "reason": reason,
            "price_scale": scale if reason is None else None, "timeframe": "D1", "source_end": end}


def build_price_location_report(snapshot):
    """Return content-addressed facts from the supplied snapshot, without mutation.

    Snapshot completion timestamps are checked against T0. Independent receipt
    authentication is deliberately not asserted by this fact layer.
    """
    reference, atr = _reference(snapshot), _atr(snapshot)
    price, scale = reference["price"], atr["price_scale"]
    levels = []
    for origin in build_price_level_evidence(snapshot):
        ready = reference["status"] == "AVAILABLE" and origin.status in {"AVAILABLE", "AVAILABLE_APPROXIMATE"}
        level = origin.price
        distance = _finite_result(level - price) if ready else None
        side = ("BELOW" if level < price else "ABOVE" if level > price else "ON") if ready else None
        percentage = _finite_result(distance / abs(price) * 100) if distance is not None and price != 0 else None
        atr_distance = _finite_result(distance / scale) if distance is not None and scale is not None else None
        levels.append({"origin": origin.to_dict(), "status": origin.status if ready else "UNAVAILABLE",
                       "reason": None if ready else reference["reason"] or origin.reason,
                       "side": side, "signed_distance_price": distance,
                       "absolute_distance_price": abs(distance) if distance is not None else None,
                       "distance_price_reason": "ARITHMETIC_OVERFLOW" if ready and distance is None else None,
                       "signed_distance_pct": percentage,
                       "distance_pct_reason": ("ZERO_REFERENCE_PRICE" if price == 0 else "ARITHMETIC_OVERFLOW") if ready and percentage is None else None,
                       "signed_distance_atr": atr_distance,
                       "distance_atr_reason": (atr["reason"] or "ARITHMETIC_OVERFLOW") if ready and atr_distance is None else None})
    def nearest(side):
        candidates = [i for i in levels if i["side"] == side]
        if not candidates:
            return None
        value = (min if side == "ABOVE" else max)(i["origin"]["price"] for i in candidates)
        tied = [i for i in candidates if i["origin"]["price"] == value]
        return {"price": value, "origins": [i["origin"] for i in tied], "absolute_distance_price": tied[0]["absolute_distance_price"]}
    body = {"version": LOCATION_FACTS_VERSION, "snapshot_id": market_snapshot_id(snapshot),
            "snapshot_sha256": hashlib.sha256(canonical_bytes(snapshot.to_dict())).hexdigest(),
            "symbol": snapshot.symbol, "secid": snapshot.secid, "t0": snapshot.as_of,
            "reference": reference, "atr_scale": atr, "levels": levels,
            "nearest_above": nearest("ABOVE"), "nearest_below": nearest("BELOW"),
            "exact_matches": [i["origin"] for i in levels if i["side"] == "ON"] if price is not None else None,
            "distance_convention": "LEVEL_MINUS_REFERENCE_PERCENT_DENOMINATOR_ABS_REFERENCE",
            "equality_rule": "EXACT_RAW_PRICE_NO_TOLERANCE", "location_label": None,
            "route": "UNAVAILABLE", "level_strength": "UNKNOWN",
            "receipt_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE"}
    return {"report_id": "location_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}


def render_price_location_report(report):
    reference = report["reference"]
    if reference["price"] is None:
        return "Положение цены недоступно: " + str(reference["reason"])
    lines = [f"Опорная цена: {reference['price']:g}; источник: {reference['timeframe']}; завершён: {reference['source_end']}."]
    for key, label in (("nearest_above", "Ближайший уровень выше"), ("nearest_below", "Ближайший уровень ниже")):
        item = report[key]
        if item is None:
            lines.append(label + ": среди допущенных уровней отсутствует.")
        else:
            distance = str(item['absolute_distance_price']) if item['absolute_distance_price'] is not None else "расчёт недоступен"
            lines.append(f"{label}: {item['price']:g}; расстояние: {distance}; источников: {len(item['origins'])}.")
    lines.append(f"Точных совпадений с ценой: {len(report['exact_matches'])}.")
    return "\n".join(lines)
