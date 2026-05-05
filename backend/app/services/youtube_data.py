"""Thin async client for YouTube Data API v3 (search, videos, commentThreads)."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_YT_BASE = "https://www.googleapis.com/youtube/v3"


def _clip(s: str | None, max_len: int) -> str:
    if not s:
        return ""
    t = s.replace("\r", " ").replace("\n", " ").strip()
    return t[:max_len] if len(t) > max_len else t


async def youtube_search_videos(
    *,
    api_key: str,
    query: str,
    max_results: int,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return normalized search items [{video_id, title, description, channel_title, published_at}], or error message."""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=settings.youtube_http_timeout_seconds)
    assert client is not None
    try:
        params = {
            "part": "snippet",
            "type": "video",
            "q": _clip(query, 200),
            "maxResults": max(1, min(50, max_results)),
            "safeSearch": "moderate",
            "key": api_key,
        }
        r = await client.get(f"{_YT_BASE}/search", params=params)
        if r.status_code != 200:
            return [], f"YouTube search HTTP {r.status_code}"
        data = r.json()
        if data.get("error"):
            err = data["error"]
            return [], str(err.get("message") or err)
        items: list[dict[str, Any]] = []
        for it in data.get("items") or []:
            vid = (it.get("id") or {}).get("videoId")
            sn = it.get("snippet") or {}
            if not vid:
                continue
            items.append(
                {
                    "video_id": vid,
                    "title": _clip(sn.get("title"), 220),
                    "description": _clip(sn.get("description"), 400),
                    "channel_title": _clip(sn.get("channelTitle"), 120),
                    "published_at": _clip(sn.get("publishedAt"), 40),
                }
            )
        return items, None
    except Exception as exc:
        logger.warning("YouTube search.list failed: %s", exc)
        return [], str(exc)
    finally:
        if own_client:
            await client.aclose()


async def youtube_videos_list(
    *,
    api_key: str,
    video_ids: list[str],
    client: httpx.AsyncClient | None = None,
) -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    """Map video_id -> {view_count, like_count, comment_count, duration, ...snippet fields}."""
    if not video_ids:
        return {}, None
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=settings.youtube_http_timeout_seconds)
    assert client is not None
    out: dict[str, dict[str, Any]] = {}
    try:
        # API allows up to 50 ids per request.
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            params = {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(chunk),
                "key": api_key,
            }
            r = await client.get(f"{_YT_BASE}/videos", params=params)
            if r.status_code != 200:
                return out, f"YouTube videos HTTP {r.status_code}"
            data = r.json()
            if data.get("error"):
                err = data["error"]
                return out, str(err.get("message") or err)
            for it in data.get("items") or []:
                vid = it.get("id")
                if not vid:
                    continue
                stats = it.get("statistics") or {}
                sn = it.get("snippet") or {}
                cd = it.get("contentDetails") or {}
                vc = stats.get("viewCount")
                view_count: int | None = None
                if vc is not None:
                    try:
                        view_count = int(vc)
                    except (TypeError, ValueError):
                        view_count = None
                out[vid] = {
                    "view_count": view_count,
                    "like_count": stats.get("likeCount"),
                    "comment_count": stats.get("commentCount"),
                    "duration": _clip(cd.get("duration"), 32),
                    "title": _clip(sn.get("title"), 220),
                    "description": _clip(sn.get("description"), 400),
                    "channel_title": _clip(sn.get("channelTitle"), 120),
                    "published_at": _clip(sn.get("publishedAt"), 40),
                }
        return out, None
    except Exception as exc:
        logger.warning("YouTube videos.list failed: %s", exc)
        return out, str(exc)
    finally:
        if own_client:
            await client.aclose()


async def youtube_comment_thread_snippets(
    *,
    api_key: str,
    video_id: str,
    max_results: int,
    client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    """Top-level comment text snippets for one video."""
    snippets: list[dict[str, Any]] = []
    try:
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max(1, min(100, max_results)),
            "textFormat": "plainText",
            "key": api_key,
        }
        r = await client.get(f"{_YT_BASE}/commentThreads", params=params)
        if r.status_code != 200:
            return snippets
        data = r.json()
        if data.get("error"):
            return snippets
        for it in data.get("items") or []:
            sn = (it.get("snippet") or {}).get("topLevelComment", {}).get("snippet") or {}
            text = _clip(sn.get("textDisplay") or sn.get("textOriginal"), 500)
            if text:
                snippets.append({"video_id": video_id, "text": text})
        return snippets
    except Exception as exc:
        logger.warning("YouTube commentThreads.list failed for %s: %s", video_id, exc)
        return snippets
