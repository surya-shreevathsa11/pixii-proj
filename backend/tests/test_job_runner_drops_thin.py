"""Unit tests for the runner-side thin-listing filter.

The full ``orchestrate`` flow needs a DB + scraping provider; here we exercise the
``_is_thin_listing`` helper and the discovery skip rule, which is where the
"Amazon product B0XXX with all-N/A" rows used to be born.
"""
from __future__ import annotations

import unittest

from app.services.scraping.base import NormalizedListing


def _listing(**overrides) -> NormalizedListing:
    base = dict(
        asin="B0TESTXYZ1",
        title="Sample Title",
        price=199.0,
        currency="INR",
        bsr_rank=42,
        bsr_category="Electronics",
        avg_rating=4.3,
        review_count=120,
        canonical_url="https://www.amazon.in/dp/B0TESTXYZ1",
        raw={"provider": "scraperapi"},
    )
    base.update(overrides)
    return NormalizedListing(**base)


class TestIsThinListing(unittest.TestCase):
    def setUp(self) -> None:
        from app.services.job_runner import _is_thin_listing

        self.is_thin = _is_thin_listing

    def test_full_listing_is_not_thin(self) -> None:
        self.assertFalse(self.is_thin(_listing()))

    def test_parse_thin_flag_marks_listing_thin_even_with_title(self) -> None:
        nl = _listing(raw={"provider": "scraperapi", "parse_thin": True})
        self.assertTrue(self.is_thin(nl))

    def test_empty_title_no_price_no_bsr_is_thin(self) -> None:
        nl = _listing(title="", price=None, bsr_rank=None)
        self.assertTrue(self.is_thin(nl))

    def test_amazon_product_placeholder_with_no_data_is_thin(self) -> None:
        nl = _listing(title="Amazon product B0FAKE9999", price=None, bsr_rank=None)
        self.assertTrue(self.is_thin(nl))

    def test_blocked_placeholder_with_no_data_is_thin(self) -> None:
        nl = _listing(title="(blocked?) B0FAKE9999", price=None, bsr_rank=None)
        self.assertTrue(self.is_thin(nl))

    def test_placeholder_title_with_price_is_not_thin(self) -> None:
        # If we have a real price + BSR, even an absent title doesn't make the row useless.
        nl = _listing(title="Amazon product B0FAKE9999", price=499.0, bsr_rank=88)
        self.assertFalse(self.is_thin(nl))


class TestDiscoverShouldSkipCompetitorTile(unittest.TestCase):
    """The iPhone-case false-skip bug: an accessory primary used to drop accessory peers."""

    def setUp(self) -> None:
        from app.services.scraping.scraperapi import ScraperApiScrapingProvider

        self.skip = ScraperApiScrapingProvider._discover_should_skip_competitor_tile

    def test_iphone_case_primary_keeps_other_iphone_cases(self) -> None:
        # The user's actual scenario: primary is an iPhone 17 Pro Max case.
        primary = "YATWIN Silicone Case for iPhone 17 Pro Max, Soft-Touch"
        peer = "Spigen iPhone 17 Pro Max Tough Armor Case, Bumper Cover"
        self.assertFalse(self.skip(primary, peer))

    def test_iphone_handset_primary_still_drops_accessory_tile(self) -> None:
        primary = "Apple iPhone 17 Pro Max 256GB Natural Titanium"
        peer = "Spigen iPhone 17 Pro Max Tough Armor Case"
        self.assertTrue(self.skip(primary, peer))

    def test_non_handset_primary_never_skipped(self) -> None:
        primary = "Sony WH-1000XM5 Wireless Headphones"
        peer = "Apple AppleCare+ for Headphones"
        # _HANDSET_PRIMARY_HINT does not match headphones, so the rule does not engage.
        self.assertFalse(self.skip(primary, peer))


if __name__ == "__main__":
    unittest.main()
