"""Apify-backed Amazon 90-day price history.

The actor slug is supplied via env (`APIFY_PRICE_HISTORY_ACTOR`); this module is shape-tolerant
about the response so different actors can be plugged in without code changes:

- list of dicts with `date`/`price` (or aliases like `timestamp`, `value`, `amount`)
- list of dicts with short keys `d` / `p`
- object containing a `priceHistory` / `prices` / `data` / `items` array

Returns `(currency, points)` where points are normalized to the last
``settings.price_history_days`` entries, sorted ascending, deduped by date.

All exceptions surface as :class:`PriceHistoryError`; the runner catches it and continues.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx
from sqlmodel import Session, select

from app.config import settings
from app.models import PriceHistory
from app.services.scraping.util import normalize_amazon_domain

logger = logging.getLogger(__name__)


class PriceHistoryError(RuntimeError):
    """Raised when Apify is unreachable, returns no usable points, or its response is malformed."""


_APIFY_ENDPOINT = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


_DATE_KEYS = ("date", "d", "timestamp", "ts", "time", "day", "dt")
_PRICE_KEYS = ("price", "p", "value", "amount", "newPrice", "current_price", "salePrice")
_CURRENCY_KEYS = ("currency", "currencyCode", "currency_code")
_LIST_KEYS = ("priceHistory", "prices", "items", "data", "history", "results")


def _coerce_price(raw: Any) -> Optional[float]:
    """Pull a positive float price out of arbitrary numeric / string shapes."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v if v > 0 else None
    if isinstance(raw, str):
        cleaned = raw.strip().replace(",", "")
        for sym in ("$", "₹", "£", "€", "¥", "USD", "INR", "GBP", "EUR"):
            cleaned = cleaned.replace(sym, "")
        cleaned = cleaned.strip()
        if not cleaned:
            return None
        try:
            v = float(cleaned)
            return v if v > 0 else None
        except ValueError:
            return None
    return None


def _coerce_date(raw: Any) -> Optional[str]:
    """Return ISO date string YYYY-MM-DD, or None if unparsable."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # Treat large integers as unix timestamps (seconds vs ms heuristic).
        seconds = float(raw)
        if seconds > 1e12:
            seconds = seconds / 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        # ISO 8601 with optional time component
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s[:10], fmt).date().isoformat()
            except ValueError:
                continue
        try:
            # Last attempt: full ISO datetime
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return None
    return None


def _first_present(d: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _flatten_records(payload: Any) -> list[dict]:
    """Find the most likely list of price records inside an arbitrary payload."""
    if isinstance(payload, list):
        # Could be list of point dicts already, or list of wrapper dicts.
        if payload and isinstance(payload[0], dict) and any(k in payload[0] for k in _DATE_KEYS):
            return payload
        # Walk one level deeper for nested arrays.
        out: list[dict] = []
        for item in payload:
            if isinstance(item, dict):
                nested = _flatten_records(item)
                if nested:
                    out.extend(nested)
        return out
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            if key in payload and isinstance(payload[key], list):
                return _flatten_records(payload[key])
        # Some actors nest under "result" / arbitrary keys; return any list of dicts at top level.
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if any(k in value[0] for k in _DATE_KEYS):
                    return value
    return []


def _extract_currency(payload: Any, records: list[dict]) -> str:
    if isinstance(payload, dict):
        for key in _CURRENCY_KEYS:
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().upper()[:8]
    for rec in records:
        for key in _CURRENCY_KEYS:
            v = rec.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().upper()[:8]
    return ""


def parse_apify_payload(payload: Any, *, today: Optional[date] = None) -> tuple[str, list[dict]]:
    """Public for unit-testing. Convert any supported actor response into normalized points.

    Returns ``(currency, [{"d": "YYYY-MM-DD", "p": 1234.0}, ...])`` sorted ascending,
    truncated to ``settings.price_history_days`` and deduped by date (last wins).
    """
    records = _flatten_records(payload)
    currency = _extract_currency(payload, records)

    today = today or datetime.now(timezone.utc).date()
    earliest = today - timedelta(days=max(1, settings.price_history_days))

    by_date: dict[str, float] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        d_iso = _coerce_date(_first_present(rec, _DATE_KEYS))
        price = _coerce_price(_first_present(rec, _PRICE_KEYS))
        if not d_iso or price is None:
            continue
        try:
            parsed = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if parsed < earliest or parsed > today:
            continue
        by_date[d_iso] = price

    points = [{"d": k, "p": v} for k, v in sorted(by_date.items())]
    return currency, points[-settings.price_history_days:]


async def fetch_apify_price_history(asin: str, amazon_domain: str) -> tuple[str, list[dict]]:
    """Run the configured Apify actor synchronously and return normalized points.

    Raises :class:`PriceHistoryError` for any failure (no token, no actor, network, parse, empty).
    """
    token = settings.apify_api_token.strip()
    actor = settings.apify_price_history_actor.strip()
    if not token:
        raise PriceHistoryError("APIFY_API_TOKEN not set")
    if not actor:
        raise PriceHistoryError("APIFY_PRICE_HISTORY_ACTOR not set")

    upper_asin = asin.strip().upper()
    if len(upper_asin) != 10:
        raise PriceHistoryError(f"invalid ASIN: {asin!r}")

    domain = normalize_amazon_domain(amazon_domain or settings.amazon_domain)
    pdp_url = f"https://www.{domain}/dp/{upper_asin}"
    body = {
        "url": pdp_url,
        "asin": upper_asin,
        "domain": domain,
        "country": _country_from_domain(domain),
        "days": settings.price_history_days,
    }

    endpoint = _APIFY_ENDPOINT.format(actor=actor)
    params = {"token": token, "format": "json"}

    timeout = httpx.Timeout(settings.apify_timeout_seconds, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, params=params, json=body)
    except httpx.HTTPError as exc:
        raise PriceHistoryError(f"network error: {exc!s}") from exc

    if resp.status_code >= 400:
        text = resp.text[:240].replace("\n", " ")
        raise PriceHistoryError(f"HTTP {resp.status_code} from Apify: {text}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise PriceHistoryError(f"non-JSON Apify response: {exc!s}") from exc

    currency, points = parse_apify_payload(payload)
    if len(points) < 2:
        raise PriceHistoryError(f"Apify returned {len(points)} usable point(s); need >= 2")
    return currency, points


def _country_from_domain(domain: str) -> str:
    """Best-effort 2-letter country guess; actors that ignore this field are unaffected."""
    d = (domain or "").lower()
    if d.endswith(".co.uk"):
        return "GB"
    if d.endswith(".com.mx"):
        return "MX"
    if d.endswith(".com.au"):
        return "AU"
    if d.endswith(".com.br"):
        return "BR"
    if d.endswith(".co.jp"):
        return "JP"
    if d.endswith(".in"):
        return "IN"
    if d.endswith(".de"):
        return "DE"
    if d.endswith(".fr"):
        return "FR"
    if d.endswith(".it"):
        return "IT"
    if d.endswith(".es"):
        return "ES"
    if d.endswith(".ca"):
        return "CA"
    if d.endswith(".nl"):
        return "NL"
    if d.endswith(".se"):
        return "SE"
    if d.endswith(".pl"):
        return "PL"
    if d.endswith(".sg"):
        return "SG"
    if d.endswith(".ae"):
        return "AE"
    return "US"


def upsert_price_history(
    session: Session,
    job_id: uuid.UUID,
    asin: str,
    currency: str,
    points: list[dict],
    *,
    source: str = "",
) -> None:
    """Insert or update the price-history row for (job_id, asin)."""
    upper = asin.strip().upper()
    existing = session.exec(
        select(PriceHistory).where(PriceHistory.job_id == job_id).where(PriceHistory.asin == upper)
    ).first()

    if existing is None:
        existing = PriceHistory(job_id=job_id, asin=upper)

    existing.currency = (currency or "").upper()[:8]
    existing.points = list(points)
    existing.source = (source or f"apify:{settings.apify_price_history_actor}")[:128]
    existing.captured_at = datetime.now(timezone.utc)
    session.add(existing)
    session.commit()
