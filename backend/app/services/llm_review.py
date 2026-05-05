import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings


logger = logging.getLogger(__name__)


_ANTHROPIC_API = "https://api.anthropic.com/v1/messages"

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


def llm_is_configured() -> bool:
    return bool(settings.anthropic_api_key.strip())


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def extract_json_blob(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fm = _JSON_FENCE_RE.search(stripped)
    payload = fm.group(1) if fm else stripped
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # Claude may prepend/explain before JSON; recover first object block.
        first = payload.find("{")
        last = payload.rfind("}")
        if first != -1 and last != -1 and last > first:
            return json.loads(payload[first : last + 1])
        raise


def _normalize_key_purchase_criteria(raw: Any) -> list[str]:
    """Accept arrays, bullet strings, or newline-delimited strings from LLM output."""
    if raw is None:
        return []
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
        return out[:14]
    if isinstance(raw, str):
        chunks = re.split(r"[\r\n]+", raw)
        out: list[str] = []
        for chunk in chunks:
            line = _BULLET_PREFIX_RE.sub("", chunk).strip(" -•*")
            if line:
                out.append(line)
        return out[:14]
    return []


def _fallback_key_purchase_criteria_from_reviews(review_lines: list[str], limit: int = 8) -> list[str]:
    """
    Deterministic fallback when Claude output is malformed:
    emit concise shopper-language snippets from review text.
    """
    picks: list[str] = []
    seen: set[str] = set()
    for line in review_lines:
        if ":" in line:
            text = line.split(":", 1)[1].strip()
        else:
            text = line.strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\bRead more\b", "", text, flags=re.I).strip(" .")
        if len(text) < 18:
            continue
        snippet = text[:140].rstrip(" ,.;:")
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        picks.append(snippet)
        if len(picks) >= limit:
            break
    return picks


def invoke_claude_text(prompt: str, *, max_tokens: int, temperature: float = 0.2) -> str:
    key = settings.anthropic_api_key.strip()
    if not key:
        raise RuntimeError("Anthropic API key missing")
    model = (settings.anthropic_model or "").strip() or "claude-haiku-4-5-20251001"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    with httpx.Client(timeout=120.0) as client:
        rsp = client.post(_ANTHROPIC_API, json=payload, headers=headers)
    rsp.raise_for_status()
    data = rsp.json()
    parts = data.get("content") or []
    txts = [str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
    return "".join(txts).strip()


async def batch_map_review_themes(product_title: str, batch_lines: list[str]) -> dict[str, Any]:
    model_on = llm_is_configured()
    text_blob = "\n".join(batch_lines[:8000])
    if not model_on:
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
        raw_txt = invoke_claude_text(prompt, max_tokens=2048, temperature=0.2)

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
    """One Claude call for a small corpus (competitive jobs, ≤10 reviews)."""
    model_on = llm_is_configured()
    # Star-only rows (Amazon returned ratings but empty bodies) must not hit Claude: it burns
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

    if not model_on:
        return CompetitiveReviewSynthesis(
            final_summary=(
                "Claude unavailable; set ANTHROPIC_API_KEY for single-pass review synthesis. "
                f"Captured {len(review_lines)} review snippets for {asin}."
            ),
            key_purchase_criteria=_fallback_key_purchase_criteria_from_reviews(review_lines),
            why_buyers_like=None,
            why_buyers_caution=None,
        )

    def _invoke() -> CompetitiveReviewSynthesis:
        raw_txt = invoke_claude_text(prompt, max_tokens=4096, temperature=0.25)
        try:
            data = extract_json_blob(raw_txt or "{}")
        except json.JSONDecodeError:
            kp_fallback = _fallback_key_purchase_criteria_from_reviews(review_lines)
            return CompetitiveReviewSynthesis(
                final_summary=(raw_txt or "Unparseable Claude response.")[:65000],
                key_purchase_criteria=kp_fallback,
                why_buyers_like=None,
                why_buyers_caution=None,
            )
        summary = str(data.get("final_summary") or "").strip()
        like = str(data.get("why_buyers_like") or "").strip() or None
        caution = str(data.get("why_buyers_caution") or "").strip() or None
        kp = _normalize_key_purchase_criteria(data.get("key_purchase_criteria"))
        if not kp:
            kp = _fallback_key_purchase_criteria_from_reviews(review_lines)
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
                "Claude single-pass synthesis failed for %s on attempt %d (transient=%s, quota=%s): %s: %s",
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
        " Claude quota/rate limit hit; wait and re-run, or increase Anthropic plan capacity."
        if last_exc and _is_quota_error(last_exc)
        else ""
    )
    return CompetitiveReviewSynthesis(
        final_summary=(
            f"Claude synthesis unavailable right now ({err_label}).{quota_hint} "
            f"Showing the {len(review_lines)} captured review snippets for {asin} only."
        ),
        key_purchase_criteria=_fallback_key_purchase_criteria_from_reviews(review_lines),
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
    model_on = llm_is_configured()

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

    if not model_on:
        return (
            "Claude unavailable; provide ANTHROPIC_API_KEY + compatible network egress for nuanced synthesis.",
            [
                "Value vs pack size",
                "Ingredient/disclosure clarity",
                "Taste/smell subjective fit",
                "Fulfillment consistency",
                "Subscription cadence readiness",
            ],
        )

    def _invoke_reduce() -> tuple[str, list[str]]:
        raw_txt = invoke_claude_text(prompt, max_tokens=4096, temperature=0.35)

        try:
            data = extract_json_blob(raw_txt or "{}")
        except json.JSONDecodeError:
            return raw_txt[:4000], []

        summary = str(data.get("final_summary") or "").strip()
        kp = _normalize_key_purchase_criteria(data.get("key_purchase_criteria"))
        if not summary:
            summary = raw_txt[:4000]
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
