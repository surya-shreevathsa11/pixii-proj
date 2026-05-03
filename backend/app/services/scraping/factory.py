from app.config import settings
from app.services.scraping.base import ScrapingProvider
from app.services.scraping.mock import MockScrapingProvider
from app.services.scraping.scraperapi import ScraperApiScrapingProvider
from app.services.scraping.util import resolve_scraperapi_country_code


def get_scraping_provider() -> ScrapingProvider:
    p = settings.scraping_provider.lower().strip()
    aliases = {"scraper_api", "scraperapi"}

    if p in aliases:
        key = settings.scraping_api_key.strip()
        if not key:
            raise ValueError("SCRAPING_API_KEY is required for ScraperAPI (set SCRAPING_PROVIDER=scraperapi)")

        country = resolve_scraperapi_country_code(settings.scraperapi_country_code, settings.amazon_domain)

        return ScraperApiScrapingProvider(
            api_key=key,
            render=settings.scraperapi_render,
            country_code=country,
            save_html_on_empty=settings.scraperapi_save_html_on_empty,
            timeout=float(settings.scraperapi_timeout_seconds),
            render_timeout=float(settings.scraperapi_render_timeout_seconds),
        )

    return MockScrapingProvider()
