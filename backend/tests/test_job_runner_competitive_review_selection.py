"""Tests for competitive review candidate selection (prefer comment-bearing rows)."""
from __future__ import annotations

import unittest

from app.services.job_runner import _choose_competitive_review_candidates
from app.services.scraping.base import NormalizedReview


def _rv(
    ext: str,
    *,
    title: str | None = None,
    body: str = "",
    date: str | None = "2026-05-01",
    has_img: bool = False,
) -> NormalizedReview:
    return NormalizedReview(
        external_id=ext,
        rating=5,
        title=title,
        body=body,
        review_date=date,
        is_verified_purchase=True,
        has_customer_images=has_img,
    )


class TestCompetitiveReviewSelection(unittest.TestCase):
    def test_prefers_rows_with_meaningful_text(self) -> None:
        buffer = [
            (0, _rv("r0", body="", has_img=True)),
            (1, _rv("r1", body="Great fit and sturdy build for daily use.", has_img=False)),
            (2, _rv("r2", body="Excellent battery backup and clear display.", has_img=False)),
        ]
        chosen = _choose_competitive_review_candidates(buffer, target=2)
        chosen_ids = [rv.external_id for _s, rv in chosen]
        self.assertEqual(chosen_ids, ["r2", "r1"])

    def test_backfills_when_not_enough_text_rows(self) -> None:
        buffer = [
            (0, _rv("r0", body="Useful and durable for commute.")),
            (1, _rv("r1", body="", has_img=True)),
            (2, _rv("r2", body="", has_img=False)),
        ]
        chosen = _choose_competitive_review_candidates(buffer, target=3)
        chosen_ids = [rv.external_id for _s, rv in chosen]
        self.assertEqual(chosen_ids[0], "r0")
        self.assertEqual(len(chosen_ids), 3)


if __name__ == "__main__":
    unittest.main()

