"""Tests for ScraperAPI thin-PDP detection and render-retry.

Exercises ``ScraperApiScrapingProvider.fetch_listing`` and the ``_is_thin_pdp_html``
helper without hitting the network, by stubbing ``_fetch_html_lenient`` per call.
"""
from __future__ import annotations

import unittest


class TestIsThinPdpHtml(unittest.TestCase):
    def setUp(self) -> None:
        from app.services.scraping.scraperapi import ScraperApiScrapingProvider

        self.cls = ScraperApiScrapingProvider

    def test_empty_body_is_thin(self) -> None:
        self.assertTrue(self.cls._is_thin_pdp_html(""))

    def test_short_stub_is_thin(self) -> None:
        self.assertTrue(self.cls._is_thin_pdp_html("<html><body>Service Unavailable</body></html>"))

    def test_pdp_with_product_title_is_not_thin(self) -> None:
        html = "<html>" + ("<div>x</div>" * 400) + '<span id="productTitle">A Product</span></html>'
        self.assertFalse(self.cls._is_thin_pdp_html(html))

    def test_pdp_with_og_title_is_not_thin(self) -> None:
        html = (
            "<html><head>" + ("<meta name='x' content='y'>" * 200)
            + '<meta property="og:title" content="A Product">'
            + "</head><body></body></html>"
        )
        self.assertFalse(self.cls._is_thin_pdp_html(html))


class TestFetchListingRenderRetry(unittest.IsolatedAsyncioTestCase):
    """Stub ``_fetch_html_lenient`` and verify the retry / thin-fallback contract."""

    def _make_provider(self):
        from app.services.scraping.scraperapi import ScraperApiScrapingProvider

        return ScraperApiScrapingProvider(api_key="test-key", render=False)

    async def test_thin_first_pass_triggers_render_retry(self) -> None:
        provider = self._make_provider()
        calls: list[dict] = []

        rendered_html = (
            "<html><body>"
            + ('<div class="x">' + "y" * 80 + "</div>") * 40
            + '<span id="productTitle">YATWIN Silicone Case for iPhone 17 Pro Max</span>'
            + "</body></html>"
        )

        async def stub(target_url, amazon_domain=None, raise_on_error=False, *, force_render=False):
            calls.append({"force_render": force_render})
            if not force_render:
                return target_url, "", 499
            return target_url, rendered_html, 200

        provider._fetch_html_lenient = stub  # type: ignore[assignment]

        listing = await provider.fetch_listing("B0FVFNS8WZ", "amazon.in")
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[0]["force_render"])
        self.assertTrue(calls[1]["force_render"])
        # The retry surfaced a real PDP title rather than the placeholder "Amazon product B0XXX"
        # the runner would have dropped.
        self.assertIn("YATWIN", listing.title)
        self.assertNotIn("Amazon product", listing.title)

    async def test_render_retry_also_thin_returns_parse_thin_listing(self) -> None:
        provider = self._make_provider()
        calls: list[dict] = []

        async def stub(target_url, amazon_domain=None, raise_on_error=False, *, force_render=False):
            calls.append({"force_render": force_render, "amazon_domain": amazon_domain})
            return target_url, "", 504

        provider._fetch_html_lenient = stub  # type: ignore[assignment]

        listing = await provider.fetch_listing("B0FAILED01", "amazon.in")
        self.assertEqual(len(calls), 3, "expected first pass + render retry + geo-relaxed retry")
        self.assertFalse(calls[0]["force_render"])
        self.assertTrue(calls[1]["force_render"])
        self.assertTrue(calls[2]["force_render"])
        self.assertIsNone(calls[2]["amazon_domain"])
        self.assertEqual(listing.title, "")
        self.assertIsNone(listing.price)
        self.assertIsNone(listing.bsr_rank)
        self.assertTrue(listing.raw.get("parse_thin"))
        # The placeholder "Amazon product B0XXX" string must NOT appear on a thin listing —
        # the runner relies on this to drop the row.
        self.assertNotIn("Amazon product", listing.title)

    async def test_blocked_html_short_circuits_to_blocked_listing(self) -> None:
        provider = self._make_provider()

        async def stub(target_url, amazon_domain=None, raise_on_error=False, *, force_render=False):
            blob = "Robot Check\nEnter the characters you see below" + " " * 20000
            return target_url, blob, 200

        provider._fetch_html_lenient = stub  # type: ignore[assignment]

        listing = await provider.fetch_listing("B0BLOCK001", "amazon.com")
        self.assertTrue(listing.title.startswith("(blocked?)"))
        self.assertTrue(listing.raw.get("parse_thin"))


if __name__ == "__main__":
    unittest.main()
