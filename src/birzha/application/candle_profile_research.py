"""Bound approximate profile research; no forecast or snapshot upgrades."""
from dataclasses import replace
import hashlib
from birzha.application.features import _finite_price
from birzha.application.level_reaction import canonical_bytes, _receipt_time
from birzha.application.level_holding import _continuity
from birzha.application.volume_profile import VolumeProfileEngine
from birzha.domain.volume_profile import PriceVolumePoint

VERSION = "CANDLE_PROFILE_RESEARCH_V1"
WINDOW = 50
METHOD = "CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1"


def build_candle_profile_research(series, *, analysis_at, source_observed_at, schedule_bytes=None):
    analysis, observed = _receipt_time(analysis_at), _receipt_time(source_observed_at)
    if observed > analysis:
        raise ValueError("source receipt exceeds research analysis time")
    if series.timeframe != "H1" or not series.instrument.symbol or not series.instrument.secid or len(series.candles) > 5000:
        raise ValueError("bounded exact-instrument H1 source required")
    source_hash = hashlib.sha256(canonical_bytes(series.to_dict())).hexdigest()
    selected = replace(series,candles=series.candles[-WINDOW:])
    status, reason, profile, continuity = "AVAILABLE_APPROXIMATE", None, None, None
    if "CANDLES" not in series.instrument.data_capabilities:
        status, reason = "NOT_APPLICABLE", "INSTRUMENT_CANDLES_NOT_DECLARED"
    elif "VOLUME" not in series.instrument.data_capabilities:
        status, reason = "NOT_APPLICABLE", "INSTRUMENT_VOLUME_NOT_DECLARED"
    elif len(selected.candles) < WINDOW:
        status, reason = "INSUFFICIENT_HISTORY", "REQUIRES_50_SUPPLIED_COMPLETED_H1_BARS"
    else:
        previous = None
        for candle in series.candles:
            begin, end = _receipt_time(candle.begin), _receipt_time(candle.end)
            if end <= begin or previous is not None and begin <= previous:
                raise ValueError("source bars must be ordered unique and nonoverlapping")
            previous = end
        points = []
        for candle in selected.candles:
            end = _receipt_time(candle.end)
            seen = _receipt_time(candle.observed_at)
            if not end <= seen <= observed:
                raise ValueError("selected bar completion/receipt exceeds source observation")
            if candle.completed is not True:
                status, reason = "UNAVAILABLE", "UNFINISHED_BAR_IN_SELECTED_WINDOW"; break
            if not all(_finite_price(v) for v in (candle.open,candle.high,candle.low,candle.close)) or not candle.low <= min(candle.open,candle.close) <= max(candle.open,candle.close) <= candle.high:
                status, reason = "UNAVAILABLE", "INVALID_OHLC_IN_SELECTED_WINDOW"; break
            if not _finite_price(candle.volume) or candle.volume < 0:
                status, reason = "UNAVAILABLE", "MISSING_OR_INVALID_VOLUME_IN_SELECTED_WINDOW"; break
            price = candle.high/3.0 + candle.low/3.0 + candle.close/3.0
            if not _finite_price(price):
                status, reason = "UNAVAILABLE", "PROFILE_ARITHMETIC_UNAVAILABLE"; break
            points.append(PriceVolumePoint(price,candle.volume))
        if reason is None:
            continuity = _continuity(selected,schedule_bytes=schedule_bytes,
                anchor=_receipt_time(selected.candles[0].begin),cutoff=analysis)
            if schedule_bytes is not None and continuity['status'] != 'MATCHES_DECLARED_SCHEDULE':
                status, reason = "UNAVAILABLE", continuity['reason']
            elif not any(p.volume > 0 for p in points):
                status, reason = "UNAVAILABLE", "NO_POSITIVE_VOLUME_IN_SELECTED_WINDOW"
            else:
                try:
                    candidate = VolumeProfileEngine(method=METHOD).build(points).to_dict()
                    numbers = [candidate[k] for k in ('val','poc','vah','total_volume','value_area_fraction')]
                    numbers += [v for b in candidate['bins'] for v in b.values()]
                    numbers += candidate['hvn'] + candidate['lvn']
                    if not all(_finite_price(v) for v in numbers) or not candidate['val'] <= candidate['poc'] <= candidate['vah'] or candidate['total_volume'] <= 0:
                        raise ValueError("invalid profile result")
                    profile = candidate
                except (ValueError,OverflowError,ZeroDivisionError):
                    status, reason = "UNAVAILABLE", "PROFILE_ARITHMETIC_UNAVAILABLE"
    body = {"version": VERSION, "mode":"OFFLINE_RESEARCH_NOT_ISSUANCE", "symbol":series.instrument.symbol,
        "secid":series.instrument.secid,"instrument":series.to_dict()["instrument"],"timeframe":"H1","analysis_at":analysis_at,
        "source_observed_at":source_observed_at,"source_series_sha256":source_hash,
        "selected_series_sha256":hashlib.sha256(canonical_bytes(selected.to_dict())).hexdigest(),
        "source_bar_count":len(series.candles),"selected_bar_count":len(selected.candles),"required_bar_count":WINDOW,
        "window_begin":selected.candles[0].begin if selected.candles else None,
        "window_end":selected.candles[-1].end if selected.candles else None,
        "window_selection":"LAST_50_SUPPLIED_H1_BARS_NO_ROW_COMPRESSION", "method":METHOD,
        "algorithm_parameters":{"bins":24,"value_area_fraction":0.70},
        "status":status,"reason":reason,"profile":profile,"declared_schedule":continuity,
        "calendar_completeness":"NOT_INDEPENDENTLY_ATTESTED", "source_authentication":"NOT_INDEPENDENTLY_ATTESTED",
        "exact_trade_profile":False,"forecast_created":False,"old_records_mutated":False,"predictive_quality":"NOT_MEASURED"}
    return {"report_id":"profile_research_"+hashlib.sha256(canonical_bytes(body)).hexdigest(),**body}


def render_candle_profile_research(report):
    reasons = {"INSTRUMENT_CANDLES_NOT_DECLARED":"Для инструмента не заявлен источник свечей; расчёт неприменим.",
        "INSTRUMENT_VOLUME_NOT_DECLARED":"Для инструмента не заявлены пригодные объёмы; профиль неприменим.",
        "REQUIRES_50_SUPPLIED_COMPLETED_H1_BARS":"Передано меньше 50 часовых свечей; окно не допущено.",
        "UNFINISHED_BAR_IN_SELECTED_WINDOW":"В выбранном окне есть незавершённая свеча.",
        "INVALID_OHLC_IN_SELECTED_WINDOW":"В выбранном окне некорректны цены свечи.",
        "MISSING_OR_INVALID_VOLUME_IN_SELECTED_WINDOW":"В выбранном окне отсутствует или некорректен объём; свечи не удаляются ради расчёта.",
        "NO_POSITIVE_VOLUME_IN_SELECTED_WINDOW":"В выбранном окне нет положительного объёма.",
        "PROFILE_ARITHMETIC_UNAVAILABLE":"Арифметика не дала пригодного конечного профиля.",
        "MISSING_EXPECTED_BARS":"Есть пропуски объявленного расписания; профиль закрыт.",
        "UNEXPECTED_BARS":"Есть свечи вне объявленного расписания; профиль закрыт."}
    lines = ["## Приблизительный объёмный профиль", "",
        "Это отдельный исследовательский расчёт по 50 последним переданным часовым свечам; старые прогнозы не изменены."]
    if report['profile'] is None:
        lines.append(reasons.get(report['reason'],"Расчёт не допущен; неподтверждённый результат не подставляется."))
    else:
        p=report['profile']
        lines.append(f"Нижняя граница: {p['val']}; ориентир наибольшего объёма: {p['poc']}; верхняя граница: {p['vah']}.")
        lines.append("Объём каждой свечи отнесён к её типичной цене. Это приближение, а не точное распределение сделок по ценам.")
    lines.extend(["Непрерывность торгового календаря и подлинность получения источника независимо не подтверждены.",
        "При отсутствии расписания расчёт относится только к переданным свечам; неизвестные пропуски не объявляются отсутствующими.",
        "Сила уровней, торговое направление и точность прогноза здесь не определяются."])
    return "\n".join(lines)
