"""Tests for the shape-tolerant Apify price-history parser.

These tests do not hit Apify; they only exercise ``parse_apify_payload`` against
the response shapes the runner is expected to handle.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta


class TestParseApifyPayload(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import price_history as ph

        self.parse = ph.parse_apify_payload
        self.today = date(2026, 5, 1)

    def _iso(self, days_ago: int) -> str:
        return (self.today - timedelta(days=days_ago)).isoformat()

    def test_list_of_date_price_records(self) -> None:
        payload = [
            {"date": self._iso(2), "price": 199.0, "currency": "USD"},
            {"date": self._iso(1), "price": 209.0, "currency": "USD"},
            {"date": self._iso(0), "price": 189.0, "currency": "USD"},
        ]
        currency, points = self.parse(payload, today=self.today)
        self.assertEqual(currency, "USD")
        self.assertEqual([p["d"] for p in points], [self._iso(2), self._iso(1), self._iso(0)])
        self.assertEqual([p["p"] for p in points], [199.0, 209.0, 189.0])

    def test_short_keys_d_p(self) -> None:
        payload = [
            {"d": self._iso(3), "p": 100},
            {"d": self._iso(1), "p": "120.50"},
        ]
        _currency, points = self.parse(payload, today=self.today)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[-1]["p"], 120.5)

    def test_wrapper_object_with_priceHistory(self) -> None:
        payload = {
            "asin": "B0TESTXYZ1",
            "currencyCode": "INR",
            "priceHistory": [
                {"timestamp": f"{self._iso(5)}T00:00:00Z", "value": 1499},
                {"timestamp": f"{self._iso(2)}T00:00:00Z", "value": 1399},
            ],
        }
        currency, points = self.parse(payload, today=self.today)
        self.assertEqual(currency, "INR")
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["d"], self._iso(5))
        self.assertEqual(points[1]["p"], 1399.0)

    def test_dedup_same_day_keeps_last(self) -> None:
        payload = [
            {"date": self._iso(1), "price": 100},
            {"date": self._iso(1), "price": 110},
            {"date": self._iso(0), "price": 105},
        ]
        _currency, points = self.parse(payload, today=self.today)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["p"], 110.0)

    def test_drops_points_outside_window(self) -> None:
        payload = [
            {"date": self._iso(200), "price": 50},  # outside 90 days
            {"date": self._iso(45), "price": 75},
            {"date": self._iso(2), "price": 80},
            {"date": self._iso(0), "price": 82},
        ]
        _currency, points = self.parse(payload, today=self.today)
        self.assertEqual(len(points), 3)
        self.assertNotIn(self._iso(200), [p["d"] for p in points])

    def test_drops_future_points(self) -> None:
        future = (self.today + timedelta(days=2)).isoformat()
        payload = [
            {"date": self._iso(1), "price": 100},
            {"date": future, "price": 999},
        ]
        _currency, points = self.parse(payload, today=self.today)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["d"], self._iso(1))

    def test_currency_missing(self) -> None:
        payload = [{"date": self._iso(1), "price": 50}, {"date": self._iso(0), "price": 51}]
        currency, points = self.parse(payload, today=self.today)
        self.assertEqual(currency, "")
        self.assertEqual(len(points), 2)

    def test_unparsable_records_dropped(self) -> None:
        payload = [
            {"date": "not-a-date", "price": 50},
            {"date": self._iso(1), "price": "INR 1,299.50"},
            {"date": self._iso(0), "price": -50},  # non-positive dropped
            {"date": self._iso(0), "price": None},
        ]
        _currency, points = self.parse(payload, today=self.today)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["p"], 1299.5)

    def test_unix_timestamp_dates(self) -> None:
        from datetime import datetime, timezone

        d1 = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()
        d2 = datetime(2026, 4, 30, tzinfo=timezone.utc).timestamp() * 1000
        payload = [
            {"date": d1, "price": 100},
            {"date": d2, "price": 95},
        ]
        _currency, points = self.parse(payload, today=self.today)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["d"], "2026-04-30")
        self.assertEqual(points[1]["d"], "2026-05-01")

    def test_empty_payload_returns_empty(self) -> None:
        currency, points = self.parse({}, today=self.today)
        self.assertEqual(currency, "")
        self.assertEqual(points, [])

    def test_nested_results_array(self) -> None:
        payload = {"results": [{"date": self._iso(1), "price": 100}, {"date": self._iso(0), "price": 110}]}
        _currency, points = self.parse(payload, today=self.today)
        self.assertEqual(len(points), 2)


if __name__ == "__main__":
    unittest.main()
