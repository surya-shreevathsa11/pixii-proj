"""Tests for BSR extraction robustness across Amazon detail layouts."""
from __future__ import annotations

import unittest

from bs4 import BeautifulSoup


class TestScraperApiBsrParser(unittest.TestCase):
    def setUp(self) -> None:
        from app.services.scraping.scraperapi import ScraperApiScrapingProvider

        self.provider = ScraperApiScrapingProvider(api_key="test-key", render=False)

    def test_table_layout_bsr(self) -> None:
        html = """
        <div id="productDetails_feature_div">
          <table>
            <tr>
              <th>Best Sellers Rank</th>
              <td>#2,345 in Electronics (See Top 100 in Electronics) #77 in Cases</td>
            </tr>
          </table>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        rank, cat = self.provider._extract_bsr(soup, html)
        self.assertEqual(rank, 2345)
        self.assertEqual(cat, "Electronics")

    def test_bullet_layout_bestsellers_spelling(self) -> None:
        html = """
        <div id="detailBullets_feature_div">
          <ul>
            <li><span class="a-list-item">Amazon Bestsellers Rank: #11,234 in Mobile Accessories (See Top 100)</span></li>
          </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        rank, cat = self.provider._extract_bsr(soup, html)
        self.assertEqual(rank, 11234)
        self.assertEqual(cat, "Mobile Accessories")


if __name__ == "__main__":
    unittest.main()

