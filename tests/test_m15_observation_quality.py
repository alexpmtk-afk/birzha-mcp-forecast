"""M15 data-integrity regression tests: no source-volume loss or false coverage."""
from datetime import datetime, timezone, timedelta
import unittest

from birzha.domain.market import Candle
from birzha.providers.moex_iss import MoexIssClient, MoexIssError


def m1(minute: int, volume: float | None, *, at: str = "10", price: float = 100.0) -> Candle:
    return Candle(
        begin=f"2026-03-19 {at}:{minute:02d}:00",
        end=f"2026-03-19 {at}:{minute:02d}:17",
        open=price, close=price + 1,
        low=price - 1, high=price + 2,
        volume=volume, value=None,
    )


CUTOFF = datetime(2026, 3, 20, 0, 0, tzinfo=timezone(timedelta(hours=3)))


class M15ObservationQualityTest(unittest.TestCase):
    def test_sparse_keeps_actual_minutes_and_all_recorded_volume(self):
        source = (m1(0, 5), m1(3, 7), m1(14, 11), m1(31, 13))
        rows = MoexIssClient._aggregate_m15(source, as_of=CUTOFF)
        self.assertEqual(len(rows), 2)
        self.assertEqual([x.volume for x in rows], [23, 13])
        self.assertEqual([x.observed_m1_count for x in rows], [3, 1])
        self.assertEqual(sum(x.volume for x in rows), sum(x.volume for x in source))
        self.assertTrue(all(x.completed for x in rows))
        self.assertTrue(all(x.observation_quality == "SPARSE_M1_UNKNOWN_GAPS" for x in rows))
        self.assertFalse(any("10:15" in x.begin for x in rows))
        self.assertEqual(rows[0].close, source[2].close)

    def test_dense_fifteen_distinct_minutes(self):
        rows = MoexIssClient._aggregate_m15(
            tuple(m1(i, 2) for i in range(15)), as_of=CUTOFF
        )
        self.assertEqual(rows[0].volume, 30)
        self.assertEqual(rows[0].observed_m1_count, 15)
        self.assertEqual(rows[0].expected_m1_count, 15)
        self.assertEqual(rows[0].observation_quality, "DENSE_15_OBSERVED")

    def test_missing_volume_remains_unknown_not_partial_sum(self):
        rows = MoexIssClient._aggregate_m15(
            (m1(0, 3), m1(1, None), m1(3, 8)), as_of=CUTOFF
        )
        self.assertIsNone(rows[0].volume)
        self.assertEqual(rows[0].observation_quality, "MISSING_SOURCE_VOLUME")

    def test_missing_turnover_never_mislabeled_as_zero(self):
        rows = MoexIssClient._aggregate_m15(
            (m1(0, 3), m1(1, 2)), as_of=CUTOFF
        )
        self.assertIsNone(rows[0].value)

    def test_duplicate_source_minute_fails_closed(self):
        with self.assertRaisesRegex(MoexIssError, "Duplicate M1"):
            MoexIssClient._aggregate_m15((m1(0, 3), m1(0, 3)), as_of=CUTOFF)

    def test_corrupt_minute_timestamp_fails_closed(self):
        bad = m1(0, 3)
        bad = Candle(
            begin="invalid", end=bad.end,
            open=bad.open, high=bad.high, low=bad.low,
            close=bad.close, value=bad.value, volume=bad.volume,
        )
        with self.assertRaisesRegex(MoexIssError, "Invalid M1"):
            MoexIssClient._aggregate_m15((bad,), as_of=CUTOFF)

    def test_current_15min_interval_is_not_completed_even_if_sparse(self):
        cutoff = datetime(2026, 3, 19, 10, 7, tzinfo=timezone(timedelta(hours=3)))
        rows = MoexIssClient._aggregate_m15((m1(0, 3), m1(3, 8)), as_of=cutoff)
        self.assertEqual(rows[0].volume, 11)
        self.assertEqual(rows[0].observation_quality, "SPARSE_M1_UNKNOWN_GAPS")
        self.assertFalse(rows[0].completed)

    def test_source_h1_serialization_remains_backward_compatible(self):
        hour = Candle(
            begin="2026-03-19 10:00:00",
            end="2026-03-19 10:31:20",
            open=100, high=103, low=99, close=101,
            volume=6, value=2000, completed=True,
        )
        packed = hour.to_dict()
        for name in ("observed_m1_count", "expected_m1_count", "observation_quality"):
            self.assertNotIn(name, packed)
        self.assertEqual(hour.end, "2026-03-19 10:31:20")

    def test_m15_quality_fields_survive_serialization(self):
        row = MoexIssClient._aggregate_m15((m1(0, 1),), as_of=CUTOFF)[0]
        packed = row.to_dict()
        self.assertEqual(packed["observed_m1_count"], 1)
        self.assertEqual(packed["expected_m1_count"], 15)
        self.assertEqual(packed["observation_quality"], "SPARSE_M1_UNKNOWN_GAPS")


if __name__ == "__main__":
    unittest.main()
