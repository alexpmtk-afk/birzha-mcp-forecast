from birzha.application.validation_readiness import (
    ValidationDataReadinessService,
    required_price_ranges,
)


class _History:
    def __init__(self, missing=()):
        self.missing = set(missing)
        self.calls = []

    def is_range_verified(self, symbol, *, timeframe, from_date, till_date):
        self.calls.append((symbol, timeframe, from_date, till_date))
        return (symbol, timeframe) not in self.missing


def test_required_price_ranges_include_causal_lookback() -> None:
    assert required_price_ranges("2025-01-01", "2026-05-31") == (
        ("D1", "2024-03-07", "2026-05-31"),
        ("H1", "2024-10-03", "2026-05-31"),
        ("M15", "2024-12-02", "2026-05-31"),
    )


def test_readiness_is_not_ready_if_one_market_timeframe_is_unverified() -> None:
    history = _History(missing={"GOLD", "M15"})
    report = ValidationDataReadinessService(history=history).check(  # type: ignore[arg-type]
        ("SBER", "GOLD"),
        validation_start="2025-01-01",
        validation_end="2026-05-31",
    )

    assert report.status == "NOT_READY"
    assert len(report.requirements) == 6
    assert [(item.symbol, item.timeframe) for item in report.missing] == [("GOLD", "M15")]


def test_readiness_is_ready_only_when_every_requirement_is_verified() -> None:
    history = _History()
    report = ValidationDataReadinessService(history=history).check(  # type: ignore[arg-type]
        ("SBER", "GOLD"),
        validation_start="2025-01-01",
        validation_end="2026-05-31",
    )

    assert report.status == "READY"
    assert report.missing == ()
    assert len(history.calls) == 6
