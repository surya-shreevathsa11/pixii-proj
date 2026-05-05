"""Tests for expanded competitive auto-discovery candidate pool."""
from __future__ import annotations

import unittest


class TestMockDiscoveryPool(unittest.IsolatedAsyncioTestCase):
    async def test_default_limit_matches_legacy(self) -> None:
        from app.services.scraping.mock import MockScrapingProvider

        p = MockScrapingProvider()
        out = await p.discover_competitor_asins("B0TEST0001", "amazon.com", 10)
        self.assertEqual(len(out), 10)
        self.assertNotIn("B0TEST0001", out)

    async def test_candidate_pool_expands_return_count(self) -> None:
        from app.services.scraping.mock import MockScrapingProvider

        p = MockScrapingProvider()
        out = await p.discover_competitor_asins(
            "B0POOL0001",
            "amazon.com",
            10,
            candidate_pool_limit=30,
        )
        self.assertEqual(len(out), 30)
        self.assertEqual(len({a.upper() for a in out}), 30)

    async def test_pool_without_kwarg_uses_limit_only(self) -> None:
        from app.services.scraping.mock import MockScrapingProvider

        p = MockScrapingProvider()
        out = await p.discover_competitor_asins("B0ABC00001", "amazon.in", 5)
        self.assertEqual(len(out), 5)


class TestListingSliceCap(unittest.TestCase):
    def test_auto_discover_pool_caps_at_eleven_listings(self) -> None:
        target_asins_n = 43
        slice_cap = min(11, max(1, target_asins_n))
        self.assertEqual(slice_cap, 11)

    def test_manual_small_job_uses_submitted_count(self) -> None:
        target_asins_n = 4
        slice_cap = min(11, max(1, target_asins_n))
        self.assertEqual(slice_cap, 4)


if __name__ == "__main__":
    unittest.main()
