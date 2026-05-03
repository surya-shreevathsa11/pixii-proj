"""
BSR→monthly-unit estimates are illustrative for a demo-only workflow.
Tune against third-party tooling (Keepa/Jungle Scout) before production use.
"""

from dataclasses import dataclass


@dataclass
class EstimateResult:
    monthly_units: float
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
        f"Demonstration formula: slice_volume (~{scale:,.0f} units/cat/mo heuristic) "
        f"/ rank^{exponent} with rank #{rank:,}. Wide confidence intervals."
    )
    return EstimateResult(monthly_units=round(units, 2), rationale=rationale)


def monthly_revenue_from_units(monthly_units: float, unit_price: float | None) -> float | None:
    if monthly_units <= 0 or unit_price is None or unit_price <= 0:
        return None
    return round(monthly_units * unit_price, 2)


async def enrich_with_keepa_if_configured(asin: str, domain: str) -> float | None:
    """Optional plumbing for Keepa: returns None unless implemented."""
    return None
