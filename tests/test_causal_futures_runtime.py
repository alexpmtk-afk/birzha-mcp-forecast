"""Cross-root runtime T-1 selection: exact outright, expiry, no current volume.

These are pure/fixture tests. Historical MOEX first-published availability
must still be independently attested for a strict point-in-time Forecast.
"""
from datetime import date
import unittest

from birzha.providers.continuous_futures import (
    ContinuousFuturesEvidenceError,
    select_completed_session_volume,
)
from birzha.providers.moex_history import MoexHistoricalFutureResolver
from birzha.providers.moex_iss import MoexIssError


def row(day, secid, root, volume, expiry, *, value=1):
    return {
        "TRADEDATE": day, "SECID": secid, "ASSETCODE": root,
        "VOLUME": volume, "VALUE": value, "LASTTRADEDATE": expiry,
        "BOARDID": "RFUD", "MINSTEP": 10,
    }


class _Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return {"history": {"columns": [], "data": []}, "_rows": self.data}


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def _request(self, path, params):
        self.queries.append(params["date"])
        return _Response(self.rows.get(params["date"], []))

    def _table(self, payload, name):
        return payload["_rows"]


class UniversalCausalFutureSelection(unittest.TestCase):
    def test_march_ri_prior_volume_and_expiry(self):
        d = date(2026, 3, 19)
        previous = [
            row("2026-03-19", "RIH6", "RI", 22643, "2026-03-19", value=10**12),
            row("2026-03-19", "RIM6", "RI", 62777, "2026-06-18", value=1),
            row("2026-03-19", "RIH6RIM6", "RI", 1000000, "2026-03-19"),
        ]
        choice = select_completed_session_volume(
            "RI", decision_session=d,
            effective_session=date(2026, 3, 20),
            decision_rows=previous,
        )
        self.assertEqual(choice.secid, "RIM6")
        self.assertEqual(choice.volume, 62777)

    def test_ri_june_and_september(self):
        cases = [
            ("2026-06-18", "2026-06-19", "RIM6", 22720, "RIU6", 48666, "2026-06-18"),
            ("2026-09-17", "2026-09-18", "RIU6", 27550, "RIZ6", 85308, "2026-09-17"),
        ]
        for prev, target, old, old_volume, new, new_volume, expiry in cases:
            with self.subTest(target=target):
                data = [row(prev, old, "RI", old_volume, expiry),
                        row(prev, new, "RI", new_volume, "2026-12-17")]
                choice = select_completed_session_volume(
                    "RI", decision_session=date.fromisoformat(prev),
                    effective_session=date.fromisoformat(target),
                    decision_rows=data,
                )
                self.assertEqual(choice.secid, new)

    def test_monthly_and_alternate_symbol_prefix(self):
        prev = date(2026, 2, 18)
        for root, old, new in [
            ("Si", "SiH6", "SiM6"),
            ("BR", "BRH6", "BRJ6"),
            ("GOLD", "GDH6", "GDM6"),
        ]:
            with self.subTest(root=root):
                cand = [row(prev.isoformat(), old, root, 100, "2026-03-19"),
                        row(prev.isoformat(), new, root, 200, "2026-06-18")]
                choice = select_completed_session_volume(
                    root, decision_session=prev,
                    effective_session=date(2026, 2, 19),
                    decision_rows=cand,
                )
                self.assertEqual(choice.secid, new)

    def test_no_same_day_volume_leakage_into_resolver(self):
        rows = {
            "2026-03-18": [
                row("2026-03-18", "RIH6", "RI", 65735, "2026-03-19", value=1),
                row("2026-03-18", "RIM6", "RI", 24059, "2026-06-18", value=1000000),
            ],
            "2026-03-19": [
                row("2026-03-19", "RIH6", "RI", 22643, "2026-03-19", value=1),
                row("2026-03-19", "RIM6", "RI", 62777, "2026-06-18", value=1000000),
            ],
        }
        client = _Client(rows)
        resolver = MoexHistoricalFutureResolver(client)
        chosen_19 = resolver.resolve("RI", date(2026, 3, 19))
        self.assertEqual(chosen_19.secid, "RIH6")
        self.assertTrue(all(d < "2026-03-19" for d in client.queries))
        self.assertIn("RECONSTRUCTED", chosen_19.source)
        chosen_20 = resolver.resolve("RI", date(2026, 3, 20))
        self.assertEqual(chosen_20.secid, "RIM6")
        self.assertTrue(all(d <= "2026-03-19" for d in client.queries))
        self.assertEqual(chosen_20.roll_policy, "MOEX_T_MINUS_1_VOLUME_EXPIRY_V1")

    def test_expiry_forces_switch_despite_old_volume(self):
        prev = date(2026, 3, 19)
        data = [row("2026-03-19", "RIH6", "RI", 100000, "2026-03-19"),
                row("2026-03-19", "RIM6", "RI", 5000, "2026-06-18")]
        res = select_completed_session_volume(
            "RI", decision_session=prev,
            effective_session=date(2026, 3, 20),
            decision_rows=data,
        )
        self.assertEqual(res.secid, "RIM6")

    def test_missing_data_fails_closed_no_same_day_fallback(self):
        client = _Client({"2026-03-19": [
            row("2026-03-19", "RIM6", "RI", 100, "2026-06-18")
        ]})
        with self.assertRaisesRegex(MoexIssError, "No completed prior"):
            MoexHistoricalFutureResolver(client).resolve("RI", date(2026, 3, 19))

    def test_calendar_and_spread_dont_win(self):
        prev = date(2026, 3, 19)
        choice = select_completed_session_volume(
            "RI", decision_session=prev,
            effective_session=date(2026, 3, 20),
            decision_rows=[
                row("2026-03-19", "RIH6RIM6", "RI", 999999, "2130-01-01"),
                row("2026-03-19", "RIM6", "RI", 5, "2026-06-18"),
            ],
        )
        self.assertEqual(choice.secid, "RIM6")

    def test_wrong_day_rejected(self):
        with self.assertRaisesRegex(ContinuousFuturesEvidenceError, "Cross-session"):
            select_completed_session_volume(
                "RI", decision_session=date(2026, 3, 19),
                effective_session=date(2026, 3, 20),
                decision_rows=[row("2026-03-20", "RIM6", "RI", 5, "2026-06-18")],
            )

    def test_invalid_or_missing_volume_rejected(self):
        for volume in [None, -5, float("nan")]:
            with self.subTest(volume=volume):
                with self.assertRaises(ContinuousFuturesEvidenceError):
                    select_completed_session_volume(
                        "RI", decision_session=date(2026, 3, 19),
                        effective_session=date(2026, 3, 20),
                        decision_rows=[row("2026-03-19", "RIM6", "RI", volume, "2026-06-18")],
                    )

    def test_no_eligible_contract_blocks(self):
        with self.assertRaisesRegex(ContinuousFuturesEvidenceError, "No eligible"):
            select_completed_session_volume(
                "RI", decision_session=date(2026, 3, 19),
                effective_session=date(2026, 3, 20),
                decision_rows=[row("2026-03-19", "RIH6", "RI", 600, "2026-03-19")],
            )

    def test_history_timeline_includes_weekend_if_history_has_trade_date(self):
        client = _Client({
            "2026-03-18": [row("2026-03-18", "RIM6", "RI", 100, "2026-06-18")],
            "2026-03-19": [row("2026-03-19", "RIM6", "RI", 100, "2026-06-18")],
            "2026-03-20": [row("2026-03-20", "RIM6", "RI", 100, "2026-06-18")],
            "2026-03-21": [row("2026-03-21", "RIM6", "RI", 100, "2026-06-18")],
            "2026-03-22": [],
        })
        result = MoexHistoricalFutureResolver(client).timeline(
            "RI", date(2026, 3, 20), date(2026, 3, 22)
        )
        self.assertEqual([d.isoformat() for d, _ in result], ["2026-03-20", "2026-03-21"])


if __name__ == "__main__":
    unittest.main()
