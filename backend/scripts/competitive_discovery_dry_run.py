#!/usr/bin/env python3
"""Print auto-discovered competitor ASIN pool for a PDP (same path as competitive jobs).

Usage (from ``backend/`` with venv activated):

    python scripts/competitive_discovery_dry_run.py B0DPS62DYH amazon.in

Uses ``SCRAPING_PROVIDER`` / ``SCRAPING_API_KEY`` from ``.env``. For live Amazon HTML,
``SCRAPERAPI_RENDER=true`` is often required on amazon.in.

The job runner requests ``competitive_discovery_pool_limit`` candidates (default 42) so
filters can still yield nine distinct peers.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


async def _run(asin: str, domain: str) -> None:
    from app.config import settings
    from app.services.scraping.factory import get_scraping_provider
    from app.services.scraping.util import normalize_amazon_domain

    dom = normalize_amazon_domain(domain)
    provider = get_scraping_provider()
    pool_lim = max(10, settings.competitive_discovery_pool_limit)
    rows = await provider.discover_competitor_asins(
        asin.strip().upper(),
        dom,
        9,
        candidate_pool_limit=pool_lim,
    )
    print(f"primary={asin.upper()}  domain={dom}")
    print(f"settings.competitive_discovery_pool_limit={settings.competitive_discovery_pool_limit}")
    print(f"returned={len(rows)} ASINs (first nine ordered for UI, full pool for job filtering):")
    for i, a in enumerate(rows):
        mark = "  " if i < 9 else "… "
        print(f"  {mark}{i + 1:2}. {a}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("asin", help="Primary ASIN (10 chars, B…)")
    p.add_argument("domain", nargs="?", default="amazon.com", help="Storefront host, e.g. amazon.in")
    args = p.parse_args()
    asyncio.run(_run(args.asin, args.domain))


if __name__ == "__main__":
    main()
