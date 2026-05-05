"""Tests for the ComparisonSpec dataclass and Claude-response parser.

The actual Claude call is not exercised here; we only verify the parser handles
well-formed payloads, garbage, and the ``title_matches`` filter that the
discovery pipeline depends on.
"""
from __future__ import annotations

import unittest


class TestParseSpec(unittest.TestCase):
    def setUp(self) -> None:
        from app.services import comparison_spec as cs

        self.parse = cs._parse_spec
        self.ComparisonSpec = cs.ComparisonSpec

    def test_well_formed_payload(self) -> None:
        payload = {
            "query": "iPhone 17 Pro Max case",
            "must_match": ["iphone 17 pro max"],
            "must_not_match": ["iphone 16", "iphone 15", "iphone 17 pro ", "iphone 17 case"],
            "rationale": "Compatibility-bound accessory; only fits 17 Pro Max.",
        }
        spec = self.parse(payload)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.query, "iPhone 17 Pro Max case")
        self.assertEqual(spec.must_match, ["iphone 17 pro max"])
        self.assertIn("iphone 16", spec.must_not_match)

    def test_query_too_short_returns_none(self) -> None:
        self.assertIsNone(self.parse({"query": "  "}))
        self.assertIsNone(self.parse({"query": "ab"}))

    def test_query_must_contain_a_letter(self) -> None:
        self.assertIsNone(self.parse({"query": "12345"}))

    def test_query_too_long_rejected(self) -> None:
        # We instruct Claude to keep the query to 3-7 words. Anything over 120 chars
        # likely means it ignored the prompt; reject so the heuristic SERP can run instead.
        spec = self.parse({"query": "abc " + "x" * 200})
        self.assertIsNone(spec)

    def test_must_match_lowercased_and_deduped(self) -> None:
        spec = self.parse(
            {
                "query": "iPhone 17 Case",
                "must_match": ["IPHONE 17", "iphone 17", "  IPhone 17  ", "case"],
            },
        )
        assert spec is not None
        self.assertEqual(spec.must_match, ["iphone 17", "case"])

    def test_must_match_caps_at_four_items(self) -> None:
        spec = self.parse(
            {
                "query": "headphones",
                "must_match": [f"token{i}" for i in range(20)],
            },
        )
        assert spec is not None
        self.assertEqual(len(spec.must_match), 4)

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(self.parse({"foo": "bar"}))


class TestTitleMatches(unittest.TestCase):
    def setUp(self) -> None:
        from app.services.comparison_spec import ComparisonSpec

        self.ComparisonSpec = ComparisonSpec

    def test_must_match_required(self) -> None:
        spec = self.ComparisonSpec(query="x", must_match=["iphone 17 pro max"])
        self.assertTrue(spec.title_matches("Spigen iPhone 17 Pro Max Tough Armor Case"))
        self.assertFalse(spec.title_matches("Spigen iPhone 16 Pro Max Tough Armor Case"))

    def test_must_not_match_disqualifies(self) -> None:
        spec = self.ComparisonSpec(
            query="x",
            must_match=["iphone 17"],
            must_not_match=["iphone 17 pro"],
        )
        self.assertTrue(spec.title_matches("Spigen iPhone 17 Tough Armor Case"))
        self.assertFalse(spec.title_matches("Spigen iPhone 17 Pro Max Case"))

    def test_empty_title_passes(self) -> None:
        # Empty hints get filtered later via the canonical PDP title; do not pre-veto.
        spec = self.ComparisonSpec(query="x", must_match=["iphone 17"])
        self.assertTrue(spec.title_matches(""))

    def test_no_filters_passes_anything(self) -> None:
        spec = self.ComparisonSpec(query="x")
        self.assertTrue(spec.title_matches("anything goes here"))

    def test_case_insensitive(self) -> None:
        spec = self.ComparisonSpec(query="x", must_match=["IPHONE 17"], must_not_match=["PRO"])
        self.assertTrue(spec.title_matches("iphone 17 case"))
        self.assertFalse(spec.title_matches("iphone 17 pro case"))


class TestInferComparisonSpecGated(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_without_api_key(self) -> None:
        from app.config import settings
        from app.services.comparison_spec import infer_comparison_spec

        original = settings.anthropic_api_key
        try:
            settings.anthropic_api_key = ""
            self.assertIsNone(await infer_comparison_spec("Sample iPhone case"))
        finally:
            settings.anthropic_api_key = original

    async def test_returns_none_for_empty_title(self) -> None:
        from app.services.comparison_spec import infer_comparison_spec

        self.assertIsNone(await infer_comparison_spec(""))


if __name__ == "__main__":
    unittest.main()
