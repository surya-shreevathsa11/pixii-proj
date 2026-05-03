"""Revenue estimation, normalized to INR.

Two estimation strategies:

1. Primary: ``previous_month_units * unit_price`` where ``previous_month_units`` is read
   from the PDP "X bought in past month" social-proof badge. This is closer to actual
   sales than rank-derived guesses.
2. Fallback: BSR-rank heuristic (``slice_volume / rank^exponent``), kept for products
   where Amazon doesn't render the badge.

All amounts are returned in INR. When a listing is priced in another currency, we
convert via a cached USD/INR rate fetched from public FX endpoints (Frankfurter
first, then open.er-api.com), falling back to ``settings.usd_to_inr_rate`` when both
fail. The static cross-rates for non-USD currencies are deliberately rough — this is
a planning tool, not a trading system.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EstimateResult:
    monthly_units: float
    rationale: str


@dataclass
class RevenueResult:
    amount_inr: Optional[float]
    basis: str  # "bought_past_month" | "bsr_heuristic" | "unknown"
    rationale: str


CATEGORY_MONTHLY_VOL = [
    ("Health & Household", 95_000.0),
    ("Personal Care", 75_000.0),
    ("Beauty & Personal Care", 80_000.0),
    ("Baby", 68_000.0),
    ("Grocery", 65_000.0),
    ("Sports & Outdoors", 72_000.0),
    ("Home & Kitchen", 115_000.0),
    ("Electronics", 250_000.0),
]


# Approximate cross-rates expressed as "1 unit of currency = X USD". Used only when
# we need to step from a non-USD/INR price into INR via USD. These are intentionally
# coarse; tighten once the project gets per-currency live FX.
_USD_PER_UNIT_FALLBACK: dict[str, float] = {
    "USD": 1.0,
    "INR": 0.012,
    "GBP": 1.27,
    "EUR": 1.08,
    "JPY": 0.0066,
    "CAD": 0.73,
    "AUD": 0.66,
    "AED": 0.27,
}


_fx_cache: dict[str, float | float] = {"rate": 0.0, "expires_at": 0.0}
_fx_lock = asyncio.Lock()


def slice_volume(bsr_category: str | None) -> float:
    cat = (bsr_category or "").lower()
    for key, vol in CATEGORY_MONTHLY_VOL:
        if key.lower() in cat:
            return vol
    return 60_000.0


def estimate_monthly_units_from_bsr(rank: int | None, category: str | None) -> EstimateResult | None:
    if rank is None or rank < 1:
        return None

    exponent = 0.88
    scale = slice_volume(category)
    units = scale / pow(float(rank), exponent)
    rationale = (
        f"BSR heuristic: slice_volume (~{scale:,.0f} units/cat/mo) / rank^{exponent} "
        f"with rank #{rank:,}. Wide confidence intervals."
    )
    return EstimateResult(monthly_units=round(units, 2), rationale=rationale)


def monthly_revenue_from_units(monthly_units: float, unit_price: float | None) -> float | None:
    """Legacy helper kept for callers that still want the unconverted product."""
    if monthly_units <= 0 or unit_price is None or unit_price <= 0:
        return None
    return round(monthly_units * unit_price, 2)


async def _fetch_frankfurter() -> Optional[float]:
    url = "https://api.frankfurter.app/latest"
    params = {"from": "USD", "to": "INR"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        rsp = await client.get(url, params=params)
    rsp.raise_for_status()
    data = rsp.json()
    rate = data.get("rates", {}).get("INR")
    return float(rate) if rate else None


async def _fetch_open_er_api() -> Optional[float]:
    url = "https://open.er-api.com/v6/latest/USD"
    async with httpx.AsyncClient(timeout=10.0) as client:
        rsp = await client.get(url)
    rsp.raise_for_status()
    data = rsp.json()
    rate = data.get("rates", {}).get("INR")
    return float(rate) if rate else None


async def get_usd_inr_rate() -> float:
    """Return USD->INR rate, cached for ``settings.fx_cache_ttl_seconds``.

    Tries two free public endpoints; falls back to ``settings.usd_to_inr_rate`` when
    both fail. Always returns a positive float so callers don't need to handle None.
    """
    now = time.time()
    cached = _fx_cache.get("rate") or 0.0
    expires_at = _fx_cache.get("expires_at") or 0.0
    if cached and now < expires_at:
        return float(cached)

    async with _fx_lock:
        now = time.time()
        cached = _fx_cache.get("rate") or 0.0
        expires_at = _fx_cache.get("expires_at") or 0.0
        if cached and now < expires_at:
            return float(cached)

        for fetcher in (_fetch_frankfurter, _fetch_open_er_api):
            try:
                rate = await fetcher()
                if rate and rate > 0:
                    _fx_cache["rate"] = float(rate)
                    _fx_cache["expires_at"] = now + max(60, settings.fx_cache_ttl_seconds)
                    logger.info("Fetched USD/INR rate %.4f via %s", rate, fetcher.__name__)
                    return float(rate)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning("FX fetch %s failed: %s", fetcher.__name__, exc)

        fallback = float(settings.usd_to_inr_rate or 83.0)
        _fx_cache["rate"] = fallback
        # Short cache when we had to fall back, so we retry sooner.
        _fx_cache["expires_at"] = now + 600
        logger.info("Using static USD/INR fallback rate %.4f", fallback)
        return fallback


def convert_to_inr(amount: float | None, currency: str | None, usd_inr_rate: float) -> Optional[float]:
    """Best-effort conversion of any storefront price into INR.

    For currencies we don't model explicitly, the amount is treated as USD so the
    caller still gets a number (loud-and-wrong > silent-and-blank for a demo).
    """
    if amount is None or amount <= 0:
        return None
    cur = (currency or "USD").strip().upper()
    if cur == "INR":
        return round(float(amount), 2)
    if cur == "USD":
        return round(float(amount) * usd_inr_rate, 2)

    usd_per_unit = _USD_PER_UNIT_FALLBACK.get(cur)
    if usd_per_unit is None:
        logger.info("Unknown currency '%s' for conversion; treating as USD.", cur)
        usd_per_unit = 1.0
    usd_amount = float(amount) * usd_per_unit
    return round(usd_amount * usd_inr_rate, 2)


def compute_revenue_inr(
    previous_month_units: int | None,
    bsr_units: float | None,
    unit_price: float | None,
    currency: str | None,
    usd_inr_rate: float,
) -> RevenueResult:
    """Pick a basis (badge > BSR) and produce an INR-denominated monthly revenue."""
    price_inr = convert_to_inr(unit_price, currency, usd_inr_rate)

    if previous_month_units and previous_month_units > 0 and price_inr and price_inr > 0:
        amount = round(previous_month_units * price_inr, 2)
        rationale = (
            f"Previous-month sales {previous_month_units:,} (Amazon \"bought in past month\" badge) "
            f"x INR {price_inr:,.2f} unit price = INR {amount:,.2f}/month."
        )
        return RevenueResult(amount_inr=amount, basis="bought_past_month", rationale=rationale)

    if bsr_units and bsr_units > 0 and price_inr and price_inr > 0:
        amount = round(bsr_units * price_inr, 2)
        rationale = (
            f"BSR-derived units {bsr_units:,.0f}/mo x INR {price_inr:,.2f} unit price = "
            f"INR {amount:,.2f}/month (badge unavailable, falling back to rank heuristic)."
        )
        return RevenueResult(amount_inr=amount, basis="bsr_heuristic", rationale=rationale)

    return RevenueResult(
        amount_inr=None,
        basis="unknown",
        rationale="Insufficient signals: no previous-month badge, no BSR, or no usable price.",
    )


async def enrich_with_keepa_if_configured(asin: str, domain: str) -> float | None:
    """Optional plumbing for Keepa: returns None unless implemented."""
    return None
