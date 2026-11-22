import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import gold_alert as ga


def quote(price, average, second=0, symbol="gold", source="test"):
    return ga.Quote(
        price=Decimal(str(price)), average=Decimal(str(average)),
        observed_at=datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=second),
        symbol=symbol, unit="บาท", source=source,
    )


class CrossingTests(unittest.TestCase):
    def setUp(self):
        self.detector = ga.CrossingDetector(cooldown=0)

    def check(self, price, avg, second, **kwargs):
        return self.detector.evaluate(quote(price, avg, second, **kwargs), check_freshness=False)

    def test_first_quote_is_baseline(self):
        self.assertIsNone(self.check(100, 100, 1))
        self.assertIsNone(self.check(99, 100, 2))

    def test_exact_touch_not_double_trigger_after_leaving(self):
        self.check(99, 100, 1)
        equality = self.check(100, 100, 2)
        self.assertEqual(equality["kind"], "เท่ากัน / แตะเส้นค่าเฉลี่ย")
        self.assertIsNone(self.check(100, 100, 3))
        self.assertIsNone(self.check(101, 100, 4))

    def test_cross_without_exact_equal(self):
        self.check(99, 100, 1)
        result = self.check(102, 100, 2)
        self.assertEqual(result["kind"], "ราคาตัดขึ้นเหนือค่าเฉลี่ย")
        self.assertIsNone(self.check(103, 100, 3))
        result = self.check(98, 100, 4)
        self.assertEqual(result["kind"], "ราคาตัดลงต่ำกว่าค่าเฉลี่ย")

    def test_touch_bounce_not_repeat(self):
        self.check(99, 100, 1)
        self.assertIsNotNone(self.check(100, 100, 2))
        self.assertIsNone(self.check(99, 100, 3))
        self.assertIsNotNone(self.check(101, 100, 4))

    def test_duplicate_and_older_timestamp_ignored(self):
        self.check(99, 100, 10)
        self.assertIsNone(self.check(101, 100, 10))
        self.assertIsNone(self.check(101, 100, 9))
        self.assertIsNotNone(self.check(101, 100, 11))

    def test_cooldown(self):
        d = ga.CrossingDetector(cooldown=60)
        d.evaluate(quote(99, 100, 1), check_freshness=False)
        self.assertIsNotNone(d.evaluate(quote(101, 100, 2), check_freshness=False))
        self.assertIsNone(d.evaluate(quote(99, 100, 3), check_freshness=False))
        self.assertIsNotNone(d.evaluate(quote(101, 100, 70), check_freshness=False))

    def test_tolerance(self):
        d = ga.CrossingDetector(tolerance=Decimal("0.05"), cooldown=0)
        d.evaluate(quote(99, 100, 1), check_freshness=False)
        result = d.evaluate(quote("99.97", "100", 2), check_freshness=False)
        self.assertIsNotNone(result)

    def test_change_source_resets_baseline(self):
        self.check(99, 100, 1)
        self.assertIsNone(self.check(101, 100, 2, source="new_feed"))
        self.assertIsNotNone(self.check(99, 100, 3, source="new_feed"))

    def test_stale_rejected(self):
        d = ga.CrossingDetector(max_quote_age=120)
        with self.assertRaisesRegex(ValueError, "เก่าเกิน"):
            d.evaluate(ga.Quote(Decimal(99), Decimal(100), ga.utc_now() - timedelta(hours=1), "gold", "บาท", "test"))

    def test_bad_quote_validation(self):
        with self.assertRaises(ValueError):
            ga.Quote.from_json({"realtime": "bad", "average": 100, "timestamp": "2026-11-16T15:00:00+07:00"})
        with self.assertRaises(ValueError):
            ga.Quote.from_json({"realtime": 100, "average": 100, "timestamp": "2026-11-16T15:00:00"})
        with self.assertRaises(ValueError):
            ga.Quote.from_json({"realtime": -2, "average": 100, "timestamp": "2026-11-16T15:00:00+07:00"})


class ServiceTests(unittest.TestCase):
    def test_alert_sends_once_and_queues_on_failure(self):
        with patch.dict("os.environ", {"ALERT_COOLDOWN_SECONDS": "0", "MAX_QUOTE_AGE_SECONDS": "100000000"}):
            service = ga.AlertService(persistent=False)
            with patch.object(ga, "send_push") as push:
                service.accept(ga.Quote(Decimal(99), Decimal(100), ga.utc_now() - timedelta(seconds=10), "gold", "บาท", "test"))
                result = service.accept(ga.Quote(Decimal(101), Decimal(100), ga.utc_now() - timedelta(seconds=9), "gold", "บาท", "test"))
                self.assertEqual(result["sent"], 1)
                self.assertEqual(result["pending"], 0)
                push.assert_called_once()

    def test_failed_push_saved_in_queue(self):
        with patch.dict("os.environ", {"ALERT_COOLDOWN_SECONDS": "0"}):
            service = ga.AlertService(persistent=False)
            with patch.object(ga, "send_push", side_effect=RuntimeError("network down")):
                service.accept(ga.Quote(Decimal(99), Decimal(100), ga.utc_now() - timedelta(seconds=10), "gold", "บาท", "test"))
                result = service.accept(ga.Quote(Decimal(101), Decimal(100), ga.utc_now() - timedelta(seconds=9), "gold", "บาท", "test"))
                self.assertEqual(result["sent"], 0)
                self.assertEqual(result["pending"], 1)


if __name__ == "__main__":
    unittest.main()
