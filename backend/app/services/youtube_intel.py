"""YouTube competitive appendix: Data API fetch + Claude query plan + consolidation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

import httpx

from app.config import settings
from app.models import Listing
from app.services.llm_review import extract_json_blob, invoke_claude_text, llm_is_configured
from app.services.youtube_data import (
    youtube_comment_thread_snippets,
    youtube_search_videos,
    youtube_videos_list,
)

logger = logging.getLogger(__name__)

_MAX_QUERY_LEN = 100
_MAX_TITLE_FOR_PLAN = 400
_REVIEWISH = re.compile(r"\b(review|unboxing|hands[\s-]?on|test|vs\.?|comparison)\b", re.I)


def _fallback_search_query(primary_title: str) -> str:
    base = re.sub(r"\s+", " ", (primary_title or "").strip())
    if not base:
        return "product review"
    suffix = " review"
    room = max(8, _MAX_QUERY_LEN - len(suffix))
    trimmed = base[:room].rstrip(" ,.-")
    return f"{trimmed}{suffix}"[: _MAX_QUERY_LEN]


def _competitor_rows_for_plan(listings: list[Listing], primary_asin: str) -> list[dict[str, str]]:
    primary_u = (primary_asin or "").upper()
    rows: list[dict[str, str]] = []
    for row in listings:
        au = (row.asin or "").upper()
        if not au or au == primary_u:
            continue
        t = (row.title or "").strip()
        if t:
            rows.append({"product_name": t[: _MAX_TITLE_FOR_PLAN]})
    return rows[:12]


async def _claude_plan_query(
    *,
    product_url: str,
    primary_asin: str,
    primary_title: str,
    category: str | None,
    competitors: list[dict[str, str]],
) -> dict[str, Any]:
    if not llm_is_configured():
        return {}
    payload = {
        "product_url": (product_url or "")[:500],
        "primary_asin": primary_asin,
        "primary_title": (primary_title or "")[: _MAX_TITLE_FOR_PLAN],
        "category": (category or "")[:280],
        "competitors": competitors,
    }
    prompt = f"""You plan a YouTube search for shopper review videos about the Amazon product below.

Input JSON:
{json.dumps(payload)[:12000]}

Return strict JSON ONLY (no markdown) with keys:
- product_display_name: string, short retail name (<=80 chars), derived from primary_title.
- youtube_search_query: string, <={_MAX_QUERY_LEN} chars, optimized for finding review/unboxing/hands-on videos. Include brand/product tokens from primary_title; add "review" if not already implied.
- competitor_brand_hints: array of 0-8 short strings (brand or product nicknames) that appear in the provided competitor titles ONLY. Do not invent brands not present in competitor title text.

Rules:
- competitor_brand_hints MUST be substrings or clear tokenizations of the given competitor titles; if unsure, return fewer hints or an empty array.
"""

    def _invoke() -> dict[str, Any]:
        raw = invoke_claude_text(prompt, max_tokens=512, temperature=0.2)
        try:
            return extract_json_blob(raw or "{{}}")
        except json.JSONDecodeError:
            return {}

    return await asyncio.to_thread(_invoke)


async def _claude_consolidate(
    *,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    if not llm_is_configured():
        return {}
    prompt = f"""You analyze YouTube search results for an Amazon product. Use ONLY the facts in the JSON below (titles, descriptions, view counts, dates, comments). Do not invent videos or numbers.

Input JSON:
{json.dumps(bundle)[:56000]}

Return strict JSON ONLY (no markdown) with keys:
- youtube_demand_score: number 0-100 — higher when top results show strong shopper interest: many views on several videos, relevant titles to the product, and a healthy spread of creators (use only provided view_count and video list).
- creator_coverage_score: number 0-100 — higher when more distinct channel_title values appear among top results (breadth); lower if one channel dominates or few videos.
- trend_freshness_score: number 0-100 — higher when published_at dates are recent (within ~18 months for most videos); lower if everything is very old. Parse ISO dates from the payload.
- top_questions: array of 5-12 strings — recurring questions or anxieties buyers ask in comments or imply in titles; each must be traceable to provided comment text or video title/description snippets.
- competitor_mentions: array of objects {{ "product_name": string, "mention_count": integer, "examples": string[] }} — count how many videos (by title+description) plausibly mention competitor products; only use product_name values from the competitors list in the payload. examples are short video titles from this payload.
- review_video_links: array of up to 6 objects {{ "url", "title", "channel", "reason" }} — best review-like videos from the payload only. url must be https://www.youtube.com/watch?v=VIDEO_ID using video_id from payload.

Scoring rubric (approximate):
- If total view_count sum across videos is null or zero for most, cap demand_score around 35.
- If only one unique channel across all videos, cap creator_coverage around 40.
- If median video age > 4 years, cap freshness around 35.

Empty arrays are allowed if the payload lacks evidence.
"""

    def _invoke() -> dict[str, Any]:
        raw = invoke_claude_text(prompt, max_tokens=4096, temperature=0.25)
        try:
            return extract_json_blob(raw or "{{}}")
        except json.JSONDecodeError:
            return {}

    return await asyncio.to_thread(_invoke)


def _heuristic_review_links(
    search_items: list[dict[str, Any]],
    video_stats: dict[str, dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for it in search_items:
        vid = it.get("video_id")
        if not vid:
            continue
        title = it.get("title") or ""
        desc = it.get("description") or ""
        st = video_stats.get(vid) or {}
        views = st.get("view_count")
        bonus = 2.0 if _REVIEWISH.search(f"{title} {desc}") else 0.0
        vnum = float(views) if isinstance(views, int) else 0.0
        scored.append((vnum + bonus * 5000, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for _score, it in scored[:limit]:
        vid = it.get("video_id")
        if not vid:
            continue
        ch = (video_stats.get(vid) or {}).get("channel_title") or it.get("channel_title") or ""
        ttl = (video_stats.get(vid) or {}).get("title") or it.get("title") or ""
        out.append(
            {
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": str(ttl)[:220],
                "channel": str(ch)[:120],
                "reason": "Ranked by view count and review-style keywords in fetched metadata.",
            }
        )
    return out


def _heuristic_questions(comment_snippets: list[dict[str, Any]], limit: int = 10) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in comment_snippets:
        text = (row.get("text") or "").strip()
        if "?" not in text or len(text) < 12:
            continue
        # Prefer shorter question-like lines
        line = text.split("\n")[0].strip()
        if len(line) > 220:
            line = line[:220] + "…"
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _normalize_mention(raw: Any, allowed_names: set[str]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    product_name = str(raw.get("product_name") or "").strip()
    if not product_name:
        return None
    if product_name.casefold() not in allowed_names:
        return None
    try:
        n = int(raw.get("mention_count") or 0)
    except (TypeError, ValueError):
        n = 0
    ex = raw.get("examples") or []
    examples = [str(x).strip()[:200] for x in ex if str(x).strip()][:4]
    return {"product_name": product_name[:240], "mention_count": max(0, n), "examples": examples}


def _normalize_video_link(raw: Any, allowed_ids: set[str]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
    vid = m.group(1) if m else ""
    if vid and vid not in allowed_ids:
        return None
    title = str(raw.get("title") or "").strip()[:220]
    channel = str(raw.get("channel") or "").strip()[:120]
    reason = str(raw.get("reason") or "").strip()[:300]
    if not url.startswith("https://www.youtube.com/watch?v=") and vid:
        url = f"https://www.youtube.com/watch?v={vid}"
    if not url.startswith("https://"):
        return None
    return {"url": url[:256], "title": title, "channel": channel, "reason": reason}


async def enrich_competitive_job_youtube_insights(
    *,
    product_url: str,
    primary_asin: str,
    primary_title: str,
    primary_category: str | None,
    listings: list[Listing],
) -> dict[str, Any] | None:
    """Build youtube_insights dict for persistence; returns None when API key missing."""
    api_keys: list[str] = []
    for key in (settings.youtube_data_api_key.strip(), settings.youtube_data_fallback_api_key.strip()):
        if key and key not in api_keys:
            api_keys.append(key)
    if not api_keys:
        return None

    competitors = _competitor_rows_for_plan(listings, primary_asin)
    plan: dict[str, Any] = {}
    try:
        plan = await _claude_plan_query(
            product_url=product_url,
            primary_asin=primary_asin,
            primary_title=primary_title,
            category=primary_category,
            competitors=competitors,
        )
    except Exception as exc:
        logger.warning("YouTube query plan (Claude) failed: %s", exc)

    raw_q = str(plan.get("youtube_search_query") or "").strip()
    if len(raw_q) > _MAX_QUERY_LEN:
        raw_q = raw_q[:_MAX_QUERY_LEN].rstrip()
    search_query = raw_q or _fallback_search_query(primary_title)
    product_display = str(plan.get("product_display_name") or "").strip()[:120] or None
    if not product_display and primary_title:
        product_display = primary_title[:120]

    max_res = max(1, min(50, settings.youtube_search_max_results))
    timeout = settings.youtube_http_timeout_seconds

    search_items: list[dict[str, Any]] = []
    video_stats: dict[str, dict[str, Any]] = {}
    comment_snippets: list[dict[str, Any]] = []
    s_err: str | None = None
    active_api_key = api_keys[0]

    async with httpx.AsyncClient(timeout=timeout) as client:
        for idx, key in enumerate(api_keys):
            search_items, s_err = await youtube_search_videos(
                api_key=key, query=search_query, max_results=max_res, client=client
            )
            if search_items or not s_err:
                active_api_key = key
                break
            if idx < len(api_keys) - 1:
                logger.warning("YouTube primary key failed for search.list; trying fallback key.")
        if s_err and not search_items:
            return {
                "product_display_name": product_display,
                "youtube_search_query_used": search_query,
                "youtube_demand_score": None,
                "creator_coverage_score": None,
                "trend_freshness_score": None,
                "top_questions": [],
                "competitor_mentions": [],
                "review_video_links": [],
                "note": None,
                "error": s_err,
            }
        if not search_items:
            return {
                "product_display_name": product_display,
                "youtube_search_query_used": search_query,
                "youtube_demand_score": None,
                "creator_coverage_score": None,
                "trend_freshness_score": None,
                "top_questions": [],
                "competitor_mentions": [],
                "review_video_links": [],
                "note": None,
                "error": s_err or "No video results for this query.",
            }

        vids = [it["video_id"] for it in search_items if it.get("video_id")]
        video_stats, v_err = await youtube_videos_list(api_key=active_api_key, video_ids=vids, client=client)
        if v_err and len(api_keys) > 1 and active_api_key == api_keys[0]:
            logger.warning("YouTube primary key failed for videos.list; trying fallback key.")
            video_stats, v_err = await youtube_videos_list(api_key=api_keys[1], video_ids=vids, client=client)
            if not v_err:
                active_api_key = api_keys[1]
        if v_err:
            logger.warning("YouTube videos.list partial: %s", v_err)

        # Rank by views for comment sampling
        ranked = sorted(
            search_items,
            key=lambda it: int((video_stats.get(it.get("video_id") or "") or {}).get("view_count") or 0),
            reverse=True,
        )
        top_n = max(1, min(5, settings.youtube_top_videos_for_comments))
        per_vid = max(5, min(30, settings.youtube_comment_threads_per_video))
        for it in ranked[:top_n]:
            vid = it.get("video_id")
            if not vid:
                continue
            try:
                rows = await youtube_comment_thread_snippets(
                    api_key=active_api_key, video_id=str(vid), max_results=per_vid, client=client
                )
                if not rows and len(api_keys) > 1 and active_api_key == api_keys[0]:
                    rows = await youtube_comment_thread_snippets(
                        api_key=api_keys[1], video_id=str(vid), max_results=per_vid, client=client
                    )
                    if rows:
                        active_api_key = api_keys[1]
                comment_snippets.extend(rows)
            except Exception as exc:
                logger.warning("commentThreads for %s: %s", vid, exc)

    merged_videos: list[dict[str, Any]] = []
    for it in search_items:
        vid = it.get("video_id")
        if not vid:
            continue
        st = video_stats.get(vid) or {}
        merged_videos.append(
            {
                "video_id": vid,
                "title": st.get("title") or it.get("title"),
                "description": st.get("description") or it.get("description"),
                "channel_title": st.get("channel_title") or it.get("channel_title"),
                "published_at": st.get("published_at") or it.get("published_at"),
                "view_count": st.get("view_count"),
            }
        )

    allowed_ids = {str(it.get("video_id")) for it in search_items if it.get("video_id")}
    allowed_names = {str(c["product_name"]).casefold() for c in competitors if str(c.get("product_name") or "").strip()}

    bundle = {
        "youtube_search_query_used": search_query,
        "primary_asin": primary_asin.upper(),
        "competitors": competitors,
        "videos": merged_videos[:25],
        "comment_snippets": comment_snippets[:80],
    }

    claude_out: dict[str, Any] = {}
    try:
        claude_out = await _claude_consolidate(bundle=bundle)
    except Exception as exc:
        logger.warning("YouTube consolidate (Claude) failed: %s", exc)

    if not llm_is_configured() or not claude_out:
        note = (
            "Claude unavailable or returned empty; scores not computed. "
            "Review links ranked from YouTube search + view counts only."
            if not llm_is_configured()
            else "Claude consolidation failed; scores not computed. Review links ranked heuristically."
        )
        return {
            "product_display_name": product_display,
            "youtube_search_query_used": search_query,
            "youtube_demand_score": None,
            "creator_coverage_score": None,
            "trend_freshness_score": None,
            "top_questions": _heuristic_questions(comment_snippets),
            "competitor_mentions": [],
            "review_video_links": _heuristic_review_links(search_items, video_stats),
            "note": note,
            "error": None if not s_err else s_err,
        }

    def _fscore(key: str) -> float | None:
        v = claude_out.get(key)
        if v is None:
            return None
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if x != x:  # NaN
            return None
        return max(0.0, min(100.0, x))

    mentions_raw = claude_out.get("competitor_mentions") or []
    mentions = []
    for item in mentions_raw:
        norm = _normalize_mention(item, allowed_names)
        if norm:
            mentions.append(norm)

    links_raw = claude_out.get("review_video_links") or []
    links: list[dict[str, Any]] = []
    for item in links_raw:
        norm = _normalize_video_link(item, allowed_ids)
        if norm:
            links.append(norm)
    if not links:
        links = _heuristic_review_links(search_items, video_stats)

    tq = claude_out.get("top_questions") or []
    top_q = [str(x).strip() for x in tq if str(x).strip()][:14]
    if not top_q:
        top_q = _heuristic_questions(comment_snippets)

    return {
        "product_display_name": product_display,
        "youtube_search_query_used": search_query,
        "youtube_demand_score": _fscore("youtube_demand_score"),
        "creator_coverage_score": _fscore("creator_coverage_score"),
        "trend_freshness_score": _fscore("trend_freshness_score"),
        "top_questions": top_q,
        "competitor_mentions": mentions[:20],
        "review_video_links": links[:8],
        "note": None,
        "error": None if not s_err else s_err,
    }
