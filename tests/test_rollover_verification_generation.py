"""Prevent old same-day futures coverage markers from approving new T-1 history."""
import unittest
from birzha.application.historical_data import (
    HistoricalDataService, ROLLING_HISTORY_VERIFICATION_VERSION, _verification_symbol
)


class _Market:
    direct_resolver = None


class _Store:
    def __init__(self, markers):
        self.markers = set(markers)
        self.checked = []

    def is_verified(self, key, timeframe, from_date, till_date):
        self.checked.append(("price", key, timeframe))
        return (key, timeframe) in self.markers

    def is_session_range_verified(self, key, from_date, till_date):
        self.checked.append(("sessions", key, None))
        return (key, "SESSIONS") in self.markers


class GenerationV3Tests(unittest.TestCase):
    def test_version_explicit_and_legacy_cannot_revalidate_current_rollovers(self):
        self.assertEqual(ROLLING_HISTORY_VERIFICATION_VERSION,
                         "ROLLING_HISTORY_V3_T_MINUS_1_EXPIRY")
        old = "Si#ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V2_ACTIVITY"
        store = _Store({(old, "D1"), (old, "SESSIONS")})
        service = HistoricalDataService(market_data=_Market(), store=store)
        self.assertFalse(service.is_range_verified(
            "Si", timeframe="D1", from_date="2026-03-01", till_date="2026-03-20"
        ))
        self.assertNotIn(("price", old, "D1"), store.checked)

    def test_current_generations_can_revalidate_but_rtsi_index_has_no_roll_marker(self):
        key = _verification_symbol("RI", "D1", is_root=True)
        store = _Store({(key, "D1"), (key, "SESSIONS")})
        service = HistoricalDataService(market_data=_Market(), store=store)
        self.assertTrue(service.is_range_verified(
            "RI", timeframe="D1", from_date="2026-03-01", till_date="2026-03-20"
        ))
        index_key = _verification_symbol("RTSI", "D1", is_root=False)
        self.assertNotIn("ROLLING_HISTORY", index_key)
        self.assertIn("ROLLING_HISTORY_V3_T_MINUS_1_EXPIRY", key)

    def test_h1_and_m15_generations_protected_too(self):
        for tf in ("H1", "M15"):
            key = _verification_symbol("GOLD", tf, is_root=True)
            self.assertIn("ROLLING_HISTORY_V3_T_MINUS_1_EXPIRY", key)
            self.assertNotIn("V2_PREWARM", key)


if __name__ == "__main__":
    unittest.main()
