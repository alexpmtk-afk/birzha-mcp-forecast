"""Research-only controlled cases; 2026 values from MOEX archived D1."""
import unittest

from research.continuous_futures_reference import (
    EvidenceError, coordinates, schedule, decision_ledger,
)


def r(close, volume, *, published_at=None):
    row = {"open": close, "high": close + 10, "low": close - 10,
           "close": close, "volume": volume}
    if published_at is not None:
        row["published_at"] = published_at
        row["is_complete"] = True
        row["available_at_confidence"] = "EXACT"
    return row


CASES = [
    ("2026-03-18", "2026-03-19", "2026-03-20", "RIH6", "RIM6",
     108850, 112320, 65735, 24059, 108930, 111500, 22643, 62777, 111820, "2026-03-19", 2570, 320),
    ("2026-06-17", "2026-06-18", "2026-06-19", "RIM6", "RIU6",
     107570, 104330, 58308, 24832, 105250, 102420, 22720, 48666, 101060, "2026-06-18", -2830, -1360),
    ("2026-09-16", "2026-09-17", "2026-09-18", "RIU6", "RIZ6",
     85470, 85210, 61213, 35291, 84500, 83790, 27550, 85308, 84650, "2026-09-17", -710, 860),
]


class TestRIContinuousFuturesReference(unittest.TestCase):
    def test_real_three_source_transition_calculations(self):
        for (d0, d1, d2, old, new,
             old0, new0, v_old0, v_new0, old1, new1, v_old1, v_new1,
             new2, expiry, expected_basis, expected_move) in CASES:
            with self.subTest(old=old, new=new):
                data = {
                    d0: {old: r(old0, v_old0), new: r(new0, v_new0)},
                    d1: {old: r(old1, v_old1), new: r(new1, v_new1)},
                    d2: {new: r(new2, 50000)},
                }
                chosen, events = schedule([d0, d1, d2], data,
                                          {old: expiry, new: "2026-12-31"},
                                          strict_pit=False)
                self.assertEqual([x["secid"] for x in chosen], [old, new])
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].basis, expected_basis)
                out = coordinates(chosen, events, analysis_as_of=d2)
                self.assertEqual(out[1]["FORWARD_close"] - out[0]["FORWARD_close"], expected_move)
                self.assertEqual(out[1]["BACK_close"] - out[0]["BACK_close"], expected_move)
                self.assertEqual(out[0]["RAW_volume"], v_old1)
                self.assertEqual(out[1]["RAW_volume"], 50000)
                self.assertAlmostEqual(out[1]["RATIO_close"] / out[0]["RATIO_close"],
                                       new2 / new1, places=12)

    def test_strict_pit_requires_publication_metadata(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        data = {d0: {"RIH6": r(100, 100), "RIM6": r(102, 50)},
                d1: {"RIH6": r(100, 100)}}
        for row in data[d0].values():
            row["is_complete"] = True
            row["available_at_confidence"] = "EXACT"
        with self.assertRaisesRegex(EvidenceError, "Publication time unknown"):
            schedule([d0, d1], data,
                     {"RIH6": "2026-03-19", "RIM6": "2026-06-18"},
                     strict_pit=True,
                     decision_cutoffs={d1: "2026-03-19T09:50:00+03:00"})

    def test_strict_pit_rejects_future_publication(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        data = {d0: {"RIH6": r(100, 100, published_at="2026-03-19T12:00:00+03:00"),
                     "RIM6": r(102, 50, published_at="2026-03-18T23:59:00+03:00")},
                d1: {"RIH6": r(100, 100)}}
        with self.assertRaisesRegex(EvidenceError, "not yet available"):
            schedule([d0, d1], data,
                     {"RIH6": "2026-03-19", "RIM6": "2026-06-18"},
                     strict_pit=True,
                     decision_cutoffs={d1: "2026-03-19T09:50:00+03:00"})

    def test_strict_pit_accepts_verified_published_before_cutoff(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        before="2026-03-18T23:59:00+03:00"
        data = {d0: {"RIH6": r(100, 100, published_at=before),
                     "RIM6": r(102, 50, published_at=before)},
                d1: {"RIH6": r(100, 100)}}
        rows, events = schedule([d0, d1], data,
                     {"RIH6": "2026-03-19", "RIM6": "2026-06-18"},
                     strict_pit=True,
                     decision_cutoffs={d1: "2026-03-19T09:50:00+03:00"})
        self.assertEqual(rows[0]["secid"], "RIH6")
        self.assertEqual(events, [])

    def test_expiration_safety_when_expiring_contract_still_leads(self):
        d0, d1 = "2026-03-19", "2026-03-20"
        data = {d0: {"RIH6": r(100, 300), "RIM6": r(105, 100)},
                d1: {"RIM6": r(106, 100)}}
        rows, _ = schedule([d0, d1], data,
                           {"RIH6": d0, "RIM6": "2026-06-18"},
                           strict_pit=False)
        self.assertEqual(rows[0]["secid"], "RIM6")

    def test_missing_new_contract_on_target_fails_closed(self):
        d0, d1 = "2026-03-19", "2026-03-20"
        data = {d0: {"RIH6": r(100, 300), "RIM6": r(105, 100)},
                d1: {}}
        with self.assertRaisesRegex(EvidenceError, "no D1"):
            schedule([d0, d1], data,
                     {"RIH6": d0, "RIM6": "2026-06-18"}, strict_pit=False)

    def test_no_positive_candidate_fails_closed(self):
        d0, d1 = "2026-03-19", "2026-03-20"
        data = {d0: {"RIM6": r(105, 0)}, d1: {"RIM6": r(106, 100)}}
        with self.assertRaisesRegex(EvidenceError, "No eligible contract"):
            schedule([d0, d1], data,
                     {"RIM6": "2026-06-18"}, strict_pit=False)

    def test_no_lookahead_from_todays_volume(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        data = {d0: {"RIH6": r(100, 300), "RIM6": r(105, 100)},
                d1: {"RIH6": r(101, 1), "RIM6": r(106, 100000)}}
        rows,_ = schedule([d0,d1],data,
                           {"RIH6": "2026-03-19", "RIM6": "2026-06-18"},
                           strict_pit=False)
        self.assertEqual(rows[0]["secid"], "RIH6")


    def test_ex_ante_choice_does_not_read_target_bar_or_target_volume(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        prior = {"RIH6": r(100, 300), "RIM6": r(105, 100)}
        without_today, _ = decision_ledger(
            [d0, d1], {d0: prior},
            {"RIH6": "2026-03-19", "RIM6": "2026-06-18"},
            strict_pit=False,
        )
        # Even if today's new contract has 100000x volume, that has no
        # bearing on yesterday's selection at today's decision cutoff.
        with_today, _ = decision_ledger(
            [d0, d1], {d0: prior, d1: {"RIM6": r(110, 10000000)}},
            {"RIH6": "2026-03-19", "RIM6": "2026-06-18"},
            strict_pit=False,
        )
        self.assertEqual(without_today, with_today)
        self.assertEqual(without_today[0]["secid"], "RIH6")

    def test_strict_pit_rejects_inferred_availability(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        data = {d0: {"RIH6": r(100, 200, published_at="2026-03-18T22:00:00+03:00")},
                d1: {"RIH6": r(101, 1)}}
        data[d0]["RIH6"]["available_at_confidence"] = "INFERRED"
        with self.assertRaisesRegex(EvidenceError, "Historical availability unverified"):
            decision_ledger(
                [d0, d1], data, {"RIH6": d1}, strict_pit=True,
                decision_cutoffs={d1: "2026-03-19T09:50:00+03:00"},
            )

    def test_strict_pit_rejects_unfinished_previous_day(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        data = {d0: {"RIH6": r(100, 200, published_at="2026-03-18T22:00:00+03:00")}}
        data[d0]["RIH6"]["is_complete"] = False
        with self.assertRaisesRegex(EvidenceError, "Unfinished prior D1"):
            decision_ledger(
                [d0, d1], data, {"RIH6": d1}, strict_pit=True,
                decision_cutoffs={d1: "2026-03-19T09:50:00+03:00"},
            )

    def test_strict_pit_switch_basis_must_have_old_publication(self):
        dates = ["2026-03-18", "2026-03-19", "2026-03-20"]
        before = "2026-03-18T22:00:00+03:00"
        today = "2026-03-19T22:00:00+03:00"
        data = {
            dates[0]: {"RIH6": r(100, 300, published_at=before),
                       "RIM6": r(104, 80, published_at=before)},
            dates[1]: {"RIH6": r(99, 400, published_at=today),
                       "RIM6": r(105, 800, published_at=today)}
        }
        data[dates[1]]["RIH6"]["available_at_confidence"] = "INFERRED"
        with self.assertRaisesRegex(EvidenceError, "Historical availability unverified"):
            decision_ledger(
                dates, data,
                {"RIH6": "2026-03-19", "RIM6": "2026-06-18"},
                strict_pit=True,
                decision_cutoffs={
                    dates[1]: "2026-03-19T09:50:00+03:00",
                    dates[2]: "2026-03-20T09:50:00+03:00",
                },
            )


    def test_documented_not_exact_is_not_a_verified_historical_first_seen(self):
        d0, d1 = "2026-03-18", "2026-03-19"
        data = {d0: {"RIH6": r(100, 200, published_at="2026-03-18T22:00:00+03:00")}}
        data[d0]["RIH6"]["available_at_confidence"] = "DOCUMENTED"
        with self.assertRaisesRegex(EvidenceError, "Historical availability unverified"):
            decision_ledger(
                [d0, d1], data, {"RIH6": d1}, strict_pit=True,
                decision_cutoffs={d1: "2026-03-19T09:50:00+03:00"},
            )


if __name__ == "__main__":
    unittest.main()
