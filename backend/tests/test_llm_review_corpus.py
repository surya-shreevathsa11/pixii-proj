"""Tests for single-pass review corpus guards (rating-only vs real text)."""
from __future__ import annotations

import unittest


class TestSinglePassCorpusHasText(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import llm_review

        self.fn = llm_review._single_pass_corpus_has_text

    def test_empty_lines_false(self) -> None:
        self.assertFalse(self.fn(["Rating 5: ", "Rating 4:  "]))

    def test_short_tail_false(self) -> None:
        self.assertFalse(self.fn(["Rating 5: short"]))

    def test_substantive_tail_true(self) -> None:
        self.assertTrue(
            self.fn(["Rating 5: Great case, fits well and feels premium in hand."]),
        )


class TestExtractJsonBlobFallback(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import llm_review

        self.extract = llm_review.extract_json_blob

    def test_recovers_json_with_leading_text(self) -> None:
        raw = (
            "Here is your output:\\n"
            '{ "final_summary": "ok", "key_purchase_criteria": ["Fast charging", "Build quality"] }'
        )
        data = self.extract(raw)
        self.assertEqual(data.get("final_summary"), "ok")
        self.assertEqual(data.get("key_purchase_criteria"), ["Fast charging", "Build quality"])


if __name__ == "__main__":
    unittest.main()
