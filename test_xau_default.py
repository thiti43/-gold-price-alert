import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from gold_alert import AlertService, CrossingDetector, Quote
from xau_default import calculate_quote, completed_closes, reset_on_gap, spot_from_data


NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def sample_spot(price=4001, asof=NOW, status="fresh"):
    return {"spot_usd_oz": price, "xau": {"price": price, "currency": "USD", "unit": "troy_oz"},
            "data_state": {"status": status, "as_of": asof.isoformat()}}


def sample_history(today_included=True):
    # 40 days in reverse order; historical bar dates before spot's date.
    points = []
    for i in range(45):
        d = (NOW.date() - timedelta(days=45-i)).isoformat()
        points.append({"d": d, "c": 4000 + i})
    if today_included:
        points.append({"d": NOW.date().isoformat(), "c": 9000})
    return {"symbol": "XAUUSD", "interval": "daily", "points": points}


class TestXAUS(unittest.TestCase):
    def test_live_sma20_uses_19_completed_and_current_spot(self):
        h = sample_history()
        q, anchor = calculate_quote(sample_spot(), h, now=NOW)
        last19 = sorted(((r["d"], r["c"]) for r in h["points"] if r["d"] < NOW.date().isoformat()))[-19:]
        expected = (sum(Decimal(str(p)) for _, p in last19) + Decimal(4001)) / 20
        self.assertEqual(q.average, expected)
        self.assertEqual(len(anchor.split(",")), 19)
        self.assertNotIn(NOW.date().isoformat(), anchor)

    def test_reject_stale_state(self):
        with self.assertRaisesRegex(ValueError, "not marked fresh"):
            spot_from_data(sample_spot(status="stale"), now=NOW)

    def test_reject_old_actual_spot_timestamp(self):
        with self.assertRaisesRegex(ValueError, "old"):
            spot_from_data(sample_spot(asof=NOW - timedelta(minutes=12)), now=NOW)

    def test_reject_different_quote_unit(self):
        d = sample_spot()
        d["xau"]["currency"] = "THB"
        with self.assertRaisesRegex(ValueError, "USD/troy oz"):
            spot_from_data(d, now=NOW)

    def test_reject_mismatch_spot_fields(self):
        d = sample_spot()
        d["xau"]["price"] = 1000
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            spot_from_data(d, now=NOW)

    def test_reject_wrong_instrument_history(self):
        d = sample_history()
        d["symbol"] = "GC=F"
        with self.assertRaisesRegex(ValueError, "XAU/USD"):
            completed_closes(d, spot_time=NOW)

    def test_reject_missing_history(self):
        with self.assertRaisesRegex(ValueError, "19"):
            completed_closes({"symbol": "XAUUSD", "points": [{"d": "2026-11-18", "c": 4010}]}, spot_time=NOW)

    def test_reject_conflicting_duplicate_daily_close(self):
        d = sample_history()
        d["points"].append({"d": "2026-11-19", "c": 9999})
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            completed_closes(d, spot_time=NOW)

    def test_cross_up(self):
        detector = CrossingDetector(cooldown=0)
        q1 = Quote(Decimal(3990), Decimal(4000), NOW, "XAU/USD", "USD/oz", "TEST")
        q2 = Quote(Decimal(4010), Decimal(4000), NOW + timedelta(seconds=60), "XAU/USD", "USD/oz", "TEST")
        self.assertIsNone(detector.evaluate(q1, check_freshness=False))
        event = detector.evaluate(q2, check_freshness=False)
        self.assertIn("ตัดขึ้น", event["kind"])

    def test_first_quote_no_notification(self):
        detector = CrossingDetector(cooldown=0)
        q = Quote(Decimal(4010), Decimal(4000), NOW, "XAU/USD", "USD/oz", "TEST")
        self.assertIsNone(detector.evaluate(q, check_freshness=False))

    def test_gap_resets_baseline(self):
        # Use a lightweight stub to verify state reset without touching files/network.
        class S:
            state = {"detector": {"last_seen_at": (NOW-timedelta(minutes=12)).isoformat(),
                                   "previous_side": -1}, "xau_anchor": "one"}
        s = S()
        q = Quote(Decimal(4010), Decimal(4000), NOW, "XAU/USD", "USD/oz", "TEST")
        reset_on_gap(s, q, anchor="one")
        self.assertEqual(s.state["detector"], {})
        self.assertEqual(s.state["xau_anchor"], "one")

    def test_anchor_change_resets_baseline(self):
        class S:
            state = {"detector": {"last_seen_at": NOW.isoformat(),
                                   "previous_side": -1}, "xau_anchor": "old"}
        s = S()
        q = Quote(Decimal(4010), Decimal(4000), NOW, "XAU/USD", "USD/oz", "TEST")
        reset_on_gap(s, q, anchor="new")
        self.assertEqual(s.state["detector"], {})


if __name__ == "__main__":
    unittest.main()
