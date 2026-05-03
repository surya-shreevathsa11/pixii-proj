import asyncio
import json
import re
from typing import Any

import google.generativeai as genai

from app.config import settings


_MODEL_STATE: dict[str, Any | None] = {"fingerprint": None, "handle": None}
_CONFIGURE_KEY: list[str | None] = [None]


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

    import logging

    logging.getLogger(__name__).warning("Gemini unavailable: %s", last_error)
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
