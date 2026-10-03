from datetime import date

from birzha.providers.moex_history import _pick_instrument


def test_historical_future_keeps_expiration_and_settlement_separate() -> None:
    rows = [
        {
            "SECID": "SiZ6",
            "BOARDID": "RFUD",
            "ASSETCODE": "Si",
            "VALUE": 1000000,
            "VOLUME": 10000,
            "OPENPOSITION": 300000,
            "SHORTNAME": "Si-12.26",
            "LASTTRADEDATE": "2026-12-17",
            "LASTDELDATE": "2026-12-18",
        }
    ]

    instrument = _pick_instrument("Si", date(2026, 10, 3), rows)

    assert instrument.last_trade_date == "2026-12-17"
    assert instrument.expiration_date == "2026-12-17"
    assert instrument.settlement_date == "2026-12-18"


def test_historical_future_does_not_invent_missing_settlement_date() -> None:
    rows = [
        {
            "SECID": "BRV6",
            "BOARDID": "RFUD",
            "ASSETCODE": "BR",
            "VALUE": 1000000,
            "VOLUME": 10000,
            "OPENPOSITION": 300000,
            "SHORTNAME": "BR-10.26",
            "LASTTRADEDATE": "2026-10-01",
            "LASTDELDATE": None,
        }
    ]

    instrument = _pick_instrument("BR", date(2026, 9, 15), rows)

    assert instrument.expiration_date == "2026-10-01"
    assert instrument.settlement_date is None
