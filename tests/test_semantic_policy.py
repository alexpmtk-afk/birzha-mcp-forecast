from birzha.application.semantic_policy import (
    CORE_SYMBOLS,
    D1_ARCHIVE_START,
    INDICATOR_FIELDS,
    ROLLING_WINDOW_DAYS,
    build_semantic_plan,
)


def test_single_brent_forecast_targets_only_br() -> None:
    plan = build_semantic_plan("Дай прогноз по Brent", as_of_date="2026-09-24")
    assert plan.intent == "FORECAST"
    assert plan.scope == "SELECTED_SYMBOLS"
    assert plan.symbols == ("BR",)
    assert plan.requirements[0].tradestats_required is True
    assert plan.requirements[0].futoi_required is True


def test_broad_market_analysis_targets_core_universe() -> None:
    plan = build_semantic_plan(
        "Оцени текущую ситуацию на рынке", as_of_date="2026-09-24"
    )
    assert plan.intent == "ANALYSIS"
    assert plan.scope == "CORE_MARKET"
    assert plan.symbols == CORE_SYMBOLS


def test_timeframe_windows_and_indicators_match_canonical_policy() -> None:
    plan = build_semantic_plan("Прогноз по Сбербанку", as_of_date="2026-09-24")
    req = plan.requirements[0]
    by_tf = {item.timeframe: item for item in req.timeframes}

    assert D1_ARCHIVE_START == "2021-01-01"
    assert ROLLING_WINDOW_DAYS == {"H1": 90, "M15": 30}
    assert by_tf["D1"].from_date == "2021-01-01"
    assert by_tf["D1"].storage_mode == "PERMANENT"
    assert by_tf["H1"].from_date == "2026-06-26"
    assert by_tf["H1"].storage_mode == "ROLLING_90D"
    assert by_tf["M15"].from_date == "2026-08-25"
    assert by_tf["M15"].storage_mode == "ROLLING_30D"

    for item in req.timeframes:
        assert item.indicator_fields == INDICATOR_FIELDS


def test_sber_uses_tradestats_but_not_futoi() -> None:
    plan = build_semantic_plan("Анализ Сбербанка", as_of_date="2026-09-24")
    req = plan.requirements[0]
    assert req.raw_volume_required is True
    assert req.tradestats_required is True
    assert req.futoi_required is False
