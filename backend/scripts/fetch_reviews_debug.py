#!/usr/bin/env python3
"""Fetch one page of Amazon reviews via the configured scraping provider (ScraperAPI in prod).

Usage (from backend/ with venv activated):

    python scripts/fetch_reviews_debug.py B0DPS62DYH amazon.in

Requires SCRAPING_PROVIDER=scraperapi, SCRAPING_API_KEY, and matching AMAZON_DOMAIN / storefront.
For amazon.in, set SCRAPERAPI_RENDER=true in .env if this script still returns zero rows.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


async def _run(asin: str, domain: str, page: int) -> None:
    from app.services.scraping.factory import get_scraping_provider
    from app.services.scraping.util import normalize_amazon_domain

    dom = normalize_amazon_domain(domain)
    provider = get_scraping_provider()
    rows, next_tok = await provider.fetch_reviews_page(asin.strip().upper(), dom, str(page))
    print(f"ASIN={asin.upper()}  domain={dom}  page={page}")
    print(f"rows={len(rows)}  next_token={next_tok!r}")
    for i, rv in enumerate(rows[:5]):
        body = (rv.body or "").replace("\n", " ").strip()[:200]
        print(f"  [{i}] rating={rv.rating}  id={rv.external_id!r}  title={(rv.title or '')[:80]!r}")
        print(f"       body={body!r}…" if len((rv.body or "")) > 200 else f"       body={body!r}")
    if len(rows) > 5:
        print(f"  … and {len(rows) - 5} more")


def main() -> None:
    p = argparse.ArgumentParser(description="Debug fetch_reviews_page for one ASIN.")
    p.add_argument("asin", help="Amazon ASIN, e.g. B0DPS62DYH")
    p.add_argument("domain", nargs="?", default="amazon.in", help="Storefront host, e.g. amazon.in")
    p.add_argument("--page", type=int, default=1, help="Review page number (default 1)")
    args = p.parse_args()
    asyncio.run(_run(args.asin, args.domain, args.page))


if __name__ == "__main__":
    main()
