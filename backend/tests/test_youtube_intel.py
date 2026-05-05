"""Tests for YouTube Data client parsing and optional-key skip."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.services.youtube_data import youtube_search_videos, youtube_videos_list
from app.services.youtube_intel import (
    _fallback_search_query,
    _normalize_mention,
    enrich_competitive_job_youtube_insights,
)


class TestFallbackSearchQuery(unittest.TestCase):
    def test_truncates_and_suffix(self) -> None:
        long = "Word " * 40
        q = _fallback_search_query(long)
        self.assertIn("review", q.lower())
        self.assertLessEqual(len(q), 100)


class TestEnrichWithoutKey(unittest.TestCase):
    def test_returns_none(self) -> None:
        async def _run() -> None:
            with patch.object(settings, "youtube_data_api_key", ""):
                out = await enrich_competitive_job_youtube_insights(
                    product_url="https://amazon.in/dp/B0TEST1234",
                    primary_asin="B0TEST1234",
                    primary_title="Sample product title",
                    primary_category="Electronics",
                    listings=[],
                )
                self.assertIsNone(out)

        asyncio.run(_run())


class TestYoutubeSearchParse(unittest.TestCase):
    def test_parses_items(self) -> None:
        async def _run() -> None:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "items": [
                    {
                        "id": {"videoId": "abc123xyz"},
                        "snippet": {
                            "title": "Unboxing widget",
                            "description": "Short text",
                            "channelTitle": "TechChannel",
                            "publishedAt": "2024-06-01T12:00:00Z",
                        },
                    }
                ]
            }

            mock_inner = MagicMock()
            mock_inner.get = AsyncMock(return_value=mock_resp)
            items, err = await youtube_search_videos(
                api_key="fake", query="widget review", max_results=5, client=mock_inner,
            )

            self.assertIsNone(err)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["video_id"], "abc123xyz")
            self.assertEqual(items[0]["channel_title"], "TechChannel")

        asyncio.run(_run())


class TestYoutubeVideosListParse(unittest.TestCase):
    def test_merges_stats(self) -> None:
        async def _run() -> None:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "items": [
                    {
                        "id": "abc123xyz",
                        "snippet": {"title": "T", "channelTitle": "C", "publishedAt": "2024-01-01T00:00:00Z"},
                        "statistics": {"viewCount": "5000", "likeCount": "10", "commentCount": "3"},
                        "contentDetails": {"duration": "PT10M"},
                    }
                ]
            }
            mock_inner = MagicMock()
            mock_inner.get = AsyncMock(return_value=mock_resp)
            out, err = await youtube_videos_list(api_key="fake", video_ids=["abc123xyz"], client=mock_inner)

            self.assertIsNone(err)
            self.assertEqual(out["abc123xyz"]["view_count"], 5000)
            self.assertEqual(out["abc123xyz"]["duration"], "PT10M")

        asyncio.run(_run())


class TestNormalizeMention(unittest.TestCase):
    def test_uses_product_name_not_asin(self) -> None:
        allowed = {"iphone 17 pro max case", "spigen ultra hybrid case"}
        raw = {
            "product_name": "iPhone 17 Pro Max Case",
            "mention_count": 3,
            "examples": ["Best iPhone 17 Pro Max cases in 2026"],
        }
        got = _normalize_mention(raw, allowed)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["product_name"], "iPhone 17 Pro Max Case")
        self.assertEqual(got["mention_count"], 3)

    def test_rejects_unknown_product_name(self) -> None:
        allowed = {"oneplus 12r case"}
        raw = {"product_name": "Random Competitor", "mention_count": 2, "examples": []}
        self.assertIsNone(_normalize_mention(raw, allowed))


if __name__ == "__main__":
    unittest.main()
