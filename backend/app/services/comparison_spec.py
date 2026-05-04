"""Gemini-driven comparison spec for competitor discovery.

Given the primary product's title (and optional category breadcrumb), one Gemini
call returns a structured spec the discovery pipeline uses:

- ``query``           - the Amazon SERP keyword the discovery should search for
                         (3-7 words, compatibility-precise: "iPhone 17 case" must
                         not become "iPhone 17 Pro case").
- ``must_match``      - lower-case substrings every candidate title MUST contain.
- ``must_not_match``  - lower-case substrings that disqualify a candidate.
- ``rationale``       - one-sentence explanation, used in logs.

All errors / unset GOOGLE_API_KEY return ``None`` and the caller falls back to
the existing regex heuristic. Network/parse failures never raise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import google.generativeai as genai

from app.config import settings
from app.services.llm_review import _get_gemini_model, _response_text, extract_json_blob

logger = logging.getLogger(__name__)


@dataclass
class ComparisonSpec:
    """Structured comparison strategy for a single competitive job."""

    query: str
    must_match: list[str] = field(default_factory=list)
    must_not_match: list[str] = field(default_factory=list)
    rationale: str = ""

    def title_matches(self, title: str) -> bool:
        """True if a candidate title satisfies must_match AND avoids must_not_match.

        Empty / unknown titles are treated as ``True`` so we don't drop tiles
        whose hint Amazon never rendered (the per-PDP filter in pass 1 handles
        those once we have the canonical title).
        """
        if not title:
            return True
        low = title.lower()
        for needle in self.must_not_match:
            n = (needle or "").lower().strip()
            if n and n in low:
                return False
        for needle in self.must_match:
            n = (needle or "").lower().strip()
            if n and n not in low:
                return False
        return True


_PROMPT_TEMPLATE = """You decide what comparable Amazon products to compare a primary product against.

Primary product title: {title}
Primary product category breadcrumb: {category}

Return STRICT JSON (no markdown, no prose) with these keys:
- query: an Amazon search query of 3-7 words for direct competitors. Match exact
  compatibility / product type. Examples:
    "iPhone 17 Pro Max case" - NOT "iPhone 17 case" (different model).
    "iPhone 17 case" - NOT "iPhone 17 Pro case" (different model).
    "wireless gaming mouse" - NOT "gaming mouse" if the product is wireless.
- must_match: array of 1-4 short lowercase substrings (each 2-30 chars) that EVERY
  competitor title must contain. Use the most specific compatibility tokens.
- must_not_match: array of up to 6 short lowercase substrings that disqualify a
  candidate. Include adjacent-but-incompatible models (e.g. for "iPhone 17 Pro Max"
  exclude "iphone 16", "iphone 15", "iphone 17 pro " with trailing space, "iphone 17 case").
- rationale: one short sentence explaining your choice.

JSON ONLY. No code fences. No commentary.
"""


def _coerce_str_list(value: Any, *, max_items: int, max_len: int = 60) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[: max_items * 3]:
        s = str(item or "").strip().lower()
        if not s or len(s) > max_len:
            continue
        if s in out:
            continue
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _parse_spec(raw: dict[str, Any]) -> Optional[ComparisonSpec]:
    query = str(raw.get("query") or "").strip()
    if not query or len(query) < 3 or len(query) > 120:
        return None
    # Avoid pathological queries that would just echo the brand name back.
    if not re.search(r"[A-Za-z]", query):
        return None

    return ComparisonSpec(
        query=query[:120],
        must_match=_coerce_str_list(raw.get("must_match"), max_items=4, max_len=40),
        must_not_match=_coerce_str_list(raw.get("must_not_match"), max_items=6, max_len=40),
        rationale=str(raw.get("rationale") or "").strip()[:240],
    )


async def infer_comparison_spec(
    title: str, category: Optional[str] = None,
) -> Optional[ComparisonSpec]:
    """One Gemini call returning the comparison spec, or ``None`` on any failure.

    Caller is expected to fall back to its existing heuristic when ``None`` is returned.
    """
    title = (title or "").strip()
    if not title:
        return None
    if not settings.google_api_key.strip():
        return None

    model = _get_gemini_model()
    if model is None:
        return None

    prompt = _PROMPT_TEMPLATE.format(
        title=title[:300],
        category=(category or "unknown")[:200],
    )

    def _invoke() -> Optional[ComparisonSpec]:
        rsp = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.1,
                "max_output_tokens": 512,
            },
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ],
        )
        raw_txt = _response_text(rsp)
        if not raw_txt:
            return None
        try:
            data = extract_json_blob(raw_txt)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return _parse_spec(data)

    try:
        spec = await asyncio.to_thread(_invoke)
    except Exception as exc:
        logger.warning("Gemini comparison-spec call failed: %s: %s", type(exc).__name__, exc)
        return None
    if spec is None:
        logger.info("Gemini returned no usable comparison spec for %r; using heuristic.", title[:96])
    else:
        logger.info(
            "Gemini comparison spec for %r → query=%r must_match=%s must_not_match=%s",
            title[:96], spec.query, spec.must_match, spec.must_not_match,
        )
    return spec
