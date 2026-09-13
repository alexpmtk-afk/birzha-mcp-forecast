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


def test_required_price_ranges_include_only_durable_d1_causal_lookback() -> None:
    assert required_price_ranges("2025-01-01", "2026-05-31") == (
        ("D1", "2024-03-07", "2026-05-31"),
    )


def test_readiness_does_not_require_h1_or_m15_persistence() -> None:
    history = _History(missing={("GOLD", "H1"), ("GOLD", "M15")})
    report = ValidationDataReadinessService(history=history).check(  # type: ignore[arg-type]
        ("SBER", "GOLD"),
        validation_start="2025-01-01",
        validation_end="2026-05-31",
    )

    assert report.status == "READY"
    assert report.missing == ()
    assert [(item.symbol, item.timeframe) for item in report.requirements] == [
        ("SBER", "D1"),
        ("GOLD", "D1"),
    ]
    assert report.to_dict()["intraday_mode"] == "ON_DEMAND_NOT_PERSISTED"


def test_readiness_is_not_ready_if_d1_is_unverified() -> None:
    history = _History(missing={("GOLD", "D1")})
    report = ValidationDataReadinessService(history=history).check(  # type: ignore[arg-type]
        ("SBER", "GOLD"),
        validation_start="2025-01-01",
        validation_end="2026-05-31",
    )

    assert report.status == "NOT_READY"
    assert [(item.symbol, item.timeframe) for item in report.missing] == [("GOLD", "D1")]
    assert len(history.calls) == 2
