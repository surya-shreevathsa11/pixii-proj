import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import google.generativeai as genai

from app.config import settings


logger = logging.getLogger(__name__)


_MODEL_STATE: dict[str, Any | None] = {"fingerprint": None, "handle": None}
_CONFIGURE_KEY: list[str | None] = [None]

# Patterns that indicate a transient (worth retrying) failure vs a hard one.
_TRANSIENT_ERROR_RX = re.compile(
    r"\b(timeout|timed\s*out|deadline|temporar|throttle|throttling|"
    r"rate\s*limit|rate_limit|quota|exhausted|resourceexhausted|"
    r"429|500|502|503|504|unavailable|reset|aborted|"
    r"connection|network|service.?unavailable|backoff)\b",
    re.I,
)

# Quota / rate-limit errors deserve a longer backoff than other transient errors.
_QUOTA_ERROR_RX = re.compile(
    r"\b(quota|exhausted|resourceexhausted|429|rate.?limit)\b",
    re.I,
)


def _is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    msg = f"{type(exc).__name__}: {exc}"
    return bool(_TRANSIENT_ERROR_RX.search(msg))


def _is_quota_error(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}"
    return bool(_QUOTA_ERROR_RX.search(msg))


def _get_gemini_model() -> genai.GenerativeModel | None:
    key = settings.google_api_key.strip()
    fingerprint = f"{key}:{settings.gemini_model}"

    if not key:
        _MODEL_STATE["handle"] = None
        _MODEL_STATE["fingerprint"] = fingerprint
        return None

    cached = _MODEL_STATE.get("handle")
    if cached and _MODEL_STATE.get("fingerprint") == fingerprint and isinstance(cached, genai.GenerativeModel):
        return cached

    if _CONFIGURE_KEY[0] != key:
        genai.configure(api_key=key)
        _CONFIGURE_KEY[0] = key

    preferred = settings.gemini_model.strip()
    fallbacks = [preferred, "gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash-latest"]

    tried: set[str] = set()
    last_error: Exception | None = None
    for name in fallbacks:
        model_name = name.strip()
        if not model_name or model_name in tried:
            continue
        tried.add(model_name)

        try:
            model_handle = genai.GenerativeModel(model_name)
            _MODEL_STATE["handle"] = model_handle
            _MODEL_STATE["fingerprint"] = fingerprint
            return model_handle
        except Exception as exc:
            last_error = exc
            continue

    logger.warning("Gemini unavailable: %s", last_error)
    _MODEL_STATE["handle"] = None
    _MODEL_STATE["fingerprint"] = fingerprint
    return None


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def extract_json_blob(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fm = _JSON_FENCE_RE.search(stripped)
    payload = fm.group(1) if fm else stripped
    return json.loads(payload)


def _response_text(rsp: Any) -> str:
    try:
        return (rsp.text or "").strip()
    except Exception:
        parts: list[str] = []
        for cand in getattr(rsp, "candidates", None) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", None) or []:
                txt = getattr(part, "text", "") or ""
                if txt:
                    parts.append(txt)
        return "".join(parts).strip()


async def batch_map_review_themes(product_title: str, batch_lines: list[str]) -> dict[str, Any]:
    model = _get_gemini_model()
    text_blob = "\n".join(batch_lines[:8000])
    if not model:
        return build_stub_map(product_title)

    prompt = f"""You summarize Amazon shopper feedback.

Product title:
{product_title[:300]}

Batched review excerpts (newline separated):
{text_blob[:64000]}

Return strict JSON ONLY (no markdown prose) shaped like:
{{
  \"themes\": [\"theme\" ...],
  \"pros\": [],
  \"cons\": [],
  \"signals\": {{
    \"drivers\": [],
    \"blockers\": [],
    \"criteria_candidates\": [
       {{ \"criterion\": \"concise PDP angle\", \"evidence_quote\": \"<=160 chars quoting paraphrases\" }},
       ...
    ]
  }}
}}

Rules:
- Produce 10-22 criteria_candidates total.
"""

    def _invoke() -> dict[str, Any]:
        rsp = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 2048,
            },
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ],
        )

        raw_txt = _response_text(rsp)

        cleaned = extract_json_blob(raw_txt) if raw_txt else {}
        cleaned.setdefault("themes", [])
        cleaned.setdefault("signals", {})
        cleaned["signals"].setdefault("criteria_candidates", [])
        return cleaned

    try:
        return await asyncio.to_thread(_invoke)
    except Exception:
        return build_stub_map(product_title)


@dataclass
class CompetitiveReviewSynthesis:
    final_summary: str
    key_purchase_criteria: list[str]
    why_buyers_like: Optional[str]
    why_buyers_caution: Optional[str]


def _single_pass_corpus_has_text(review_lines: list[str]) -> bool:
    """True when at least one line has substantive text after the ``Rating N:`` prefix."""
    for line in review_lines:
        if ":" not in line:
            continue
        rest = line.split(":", 1)[-1].strip()
        if len(rest) >= 12:
            return True
    return False


async def synthesize_reviews_single_pass(
    asin: str, product_title: str, review_lines: list[str],
) -> CompetitiveReviewSynthesis:
    """One Gemini call for a small corpus (competitive jobs, ≤10 reviews)."""
    model = _get_gemini_model()
    # Star-only rows (Amazon returned ratings but empty bodies) must not hit Gemini: it burns
    # quota and often returns long non-JSON essays about "absence of data" instead of useful KPC.
    if not _single_pass_corpus_has_text(review_lines):
        n = len(review_lines)
        return CompetitiveReviewSynthesis(
            final_summary=(
                f"No usable review text was captured for {asin} ({n} rating row(s) with empty titles/bodies). "
                "Key purchase criteria cannot be grounded in shopper language. "
                "Try SCRAPERAPI_RENDER=true, re-run the job, or check that reviews are visible on the live PDP."
            ),
            key_purchase_criteria=[
                "Re-run with reliable review-text scraping—criteria need quoted shopper feedback.",
            ],
            why_buyers_like=(
                f"Ratings were synced but not review prose ({n} row(s)). "
                "Positive star counts alone do not reveal which product attributes buyers value."
            ),
            why_buyers_caution=None,
        )

    filtered = [ln for ln in review_lines[:32] if _single_pass_corpus_has_text([ln])]
    blob = "\n".join(filtered)[:64000]

    prompt = f"""You are synthesizing Amazon customer reviews for merchandising.

ASIN: {asin}
Product title: {product_title[:400]}

Reviews (rating-prefixed lines; empty lines omitted):
{blob}

Return strict JSON ONLY (no markdown) with keys:
- final_summary: string (about 900-1800 characters) on motivators, friction, and how this compares to typical expectations in the category.
- why_buyers_like: string (400-1200 characters) on positive patterns buyers repeat.
- why_buyers_caution: string (400-1200 characters) on recurring complaints, risks, or disappointment themes.
- key_purchase_criteria: array of 5-10 short PDP bullets the listing should address.

Ground every theme in the provided review lines; do not invent specs not hinted at in the text.
"""

    if not model:
        return CompetitiveReviewSynthesis(
            final_summary=(
                "Gemini unavailable; set GOOGLE_API_KEY for single-pass review synthesis. "
                f"Captured {len(review_lines)} review snippets for {asin}."
            ),
            key_purchase_criteria=[],
            why_buyers_like=None,
            why_buyers_caution=None,
        )

    def _invoke() -> CompetitiveReviewSynthesis:
        rsp = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.25,
                "max_output_tokens": 4096,
            },
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ],
        )
        raw_txt = _response_text(rsp)
        try:
            data = extract_json_blob(raw_txt or "{}")
        except json.JSONDecodeError:
            return CompetitiveReviewSynthesis(
                final_summary=(raw_txt or "Unparseable Gemini response.")[:65000],
                key_purchase_criteria=[],
                why_buyers_like=None,
                why_buyers_caution=None,
            )
        summary = str(data.get("final_summary") or "").strip()
        like = str(data.get("why_buyers_like") or "").strip() or None
        caution = str(data.get("why_buyers_caution") or "").strip() or None
        kp = [str(x).strip() for x in (data.get("key_purchase_criteria") or []) if str(x).strip()]
        if not summary:
            summary = (raw_txt or "")[:65000]
        return CompetitiveReviewSynthesis(
            final_summary=summary,
            key_purchase_criteria=kp[:14],
            why_buyers_like=like,
            why_buyers_caution=caution,
        )

    last_exc: Exception | None = None
    # Up to 4 attempts. Quota / ResourceExhausted gets longer backoffs so the free-tier
    # per-minute bucket can refill; competitive jobs fire one call per ASIN in a tight loop,
    # so callers also stagger between ASINs.
    quota_backoffs = [12.0, 35.0, 55.0]
    transient_backoffs = [2.0, 5.0, 10.0]
    for attempt in range(4):
        try:
            return await asyncio.to_thread(_invoke)
        except Exception as exc:
            last_exc = exc
            transient = _is_transient_error(exc)
            quota = _is_quota_error(exc)
            logger.warning(
                "Gemini single-pass synthesis failed for %s on attempt %d (transient=%s, quota=%s): %s: %s",
                asin,
                attempt + 1,
                transient,
                quota,
                type(exc).__name__,
                exc,
            )
            if attempt < 3 and transient:
                idx = min(attempt, len(quota_backoffs) - 1)
                delay = quota_backoffs[idx] if quota else transient_backoffs[idx]
                await asyncio.sleep(delay)
                continue
            break

    err_label = type(last_exc).__name__ if last_exc else "UnknownError"
    quota_hint = (
        " The Gemini free tier hit its per-minute quota; wait ~1 minute and re-run, or set a paid GOOGLE_API_KEY."
        if last_exc and _is_quota_error(last_exc)
        else ""
    )
    return CompetitiveReviewSynthesis(
        final_summary=(
            f"Gemini synthesis unavailable right now ({err_label}).{quota_hint} "
            f"Showing the {len(review_lines)} captured review snippets for {asin} only."
        ),
        key_purchase_criteria=[],
        why_buyers_like=None,
        why_buyers_caution=None,
    )


def build_stub_map(title: str) -> dict[str, Any]:
    return {
        "themes": ["pricing sensitivity", "taste/smell/profile", "label clarity"],
        "pros": ["demo mode synthesizes illustrative themes"],
        "cons": ["API key missing blocked deeper extraction"],
        "signals": {
            "drivers": ["value", "ingredient transparency"],
            "blockers": ["ambiguous serving directions"],
            "criteria_candidates": [
                {"criterion": "Ingredient transparency", "evidence_quote": "Users mention label clarity repeatedly."},
                {"criterion": "Price/value", "evidence_quote": "Repeated budget commentary across demo reviews."},
            ],
        },
    }


async def reduce_review_map(asin: str, product_title: str, batches: list[dict[str, Any]]) -> tuple[str, list[str]]:
    model = _get_gemini_model()

    condensed: list[dict[str, Any]] = []
    for chunk in batches[:12]:
        condensed.append({"themes": chunk.get("themes", []), "signals": chunk.get("signals", {})})

    serialized = json.dumps(condensed)

    prompt = f"""You consolidate batch-level review analytics for SKU {asin}.
Product title: {product_title[:280]}

Batch analytics JSON:
{serialized[:57000]}

Return strict JSON ONLY (no markdown) with keys:
- final_summary: string (roughly 1400-2200 characters) describing motivators, friction, unmet needs, and competitive deltas.
- key_purchase_criteria: array of 5-12 crisp merchandising bullets an Amazon PDP should highlight.
"""

    if not model:
        return (
            "Gemini unavailable; provide GOOGLE_API_KEY + compatible network egress for nuanced synthesis.",
            [
                "Value vs pack size",
                "Ingredient/disclosure clarity",
                "Taste/smell subjective fit",
                "Fulfillment consistency",
                "Subscription cadence readiness",
            ],
        )

    def _invoke_reduce() -> tuple[str, list[str]]:
        rsp = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.35,
                "max_output_tokens": 4096,
            },
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ],
        )

        raw_txt = _response_text(rsp)

        try:
            data = extract_json_blob(raw_txt or "{}")
        except json.JSONDecodeError:
            return raw_txt[:4000], []

        summary = str(data.get("final_summary") or "").strip()
        kp = list(data.get("key_purchase_criteria") or [])
        if not summary:
            summary = raw_txt[:4000]
        kp = [str(item).strip() for item in kp if str(item).strip()]
        return summary, kp[:14]

    try:
        return await asyncio.to_thread(_invoke_reduce)
    except Exception:
        return (
            f"Reduction failed safely for {asin}; inspect aggregated map batches downstream.",
            [
                "Value vs comparative listings",
                "Trust/safety cues",
                "Packaging ergonomics",
            ],
        )


def format_review_batches(reviews_body: list[str], rating: list[int | None], batch_size: int) -> list[list[str]]:
    lines: list[str] = []
    for body, sr in zip(reviews_body, rating, strict=False):
        rr = sr if sr is not None else "?"
        safe = body.replace("\n", " ").strip()
        lines.append(f"Rating {rr}: {safe}")
    return [lines[i : i + batch_size] for i in range(0, len(lines), batch_size)]
