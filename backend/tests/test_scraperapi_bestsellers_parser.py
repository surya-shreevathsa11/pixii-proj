"""Tests for strict Best Sellers ASIN extraction and rank ordering."""
from __future__ import annotations

import unittest


class TestBestSellersAsinExtraction(unittest.IsolatedAsyncioTestCase):
    def _make_provider(self):
        from app.services.scraping.scraperapi import ScraperApiScrapingProvider

        return ScraperApiScrapingProvider(api_key="test-key", render=False)

    async def test_prefers_ranked_tiles_and_keeps_order(self) -> None:
        provider = self._make_provider()
        html = """
        <html><body>
          <div id="zg">
            <ol id="zg-ordered-list">
              <li class="zg-item-immersion"><div data-asin="B0AAA11111"></div></li>
              <li class="zg-item-immersion"><div data-asin="B0AAA22222"></div></li>
              <li class="zg-item-immersion"><div data-asin="B0AAA33333"></div></li>
            </ol>
          </div>
          <!-- noise outside ranked area -->
          <div data-asin="B0NOISE9999"></div>
        </body></html>
        """

        async def stub_fetch_html(_target_url, _amazon_domain=None):
            return "https://www.amazon.in/gp/bestsellers/electronics", html

        provider._fetch_html = stub_fetch_html  # type: ignore[assignment]
        out = await provider.fetch_best_seller_asins("https://www.amazon.in/gp/bestsellers/electronics", "amazon.in", 10)
        self.assertGreaterEqual(len(out), 3)
        self.assertEqual(out[:3], ["B0AAA11111", "B0AAA22222", "B0AAA33333"])

    async def test_respects_limit_top_10(self) -> None:
        provider = self._make_provider()
        items = "".join(
            f'<li class="zg-item-immersion"><div data-asin="B0A{i:07d}"></div></li>'
            for i in range(1, 15)
        )
        html = f'<html><body><div id="zg"><ol id="zg-ordered-list">{items}</ol></div></body></html>'

        async def stub_fetch_html(_target_url, _amazon_domain=None):
            return "https://www.amazon.in/gp/bestsellers/electronics", html

        provider._fetch_html = stub_fetch_html  # type: ignore[assignment]
        out = await provider.fetch_best_seller_asins("https://www.amazon.in/gp/bestsellers/electronics", "amazon.in", 10)
        self.assertEqual(len(out), 10)
        self.assertEqual(out[0], "B0A0000001")
        self.assertEqual(out[-1], "B0A0000010")


if __name__ == "__main__":
    unittest.main()

