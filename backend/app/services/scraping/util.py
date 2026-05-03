def normalize_amazon_domain(domain: str) -> str:
    d = domain.strip().lower()
    d = d.replace("https://", "").replace("http://", "").replace("www.", "")
    d = d.split("/")[0].strip()
    return d or "amazon.com"


def amazon_site_origin(domain: str) -> str:
    """Https origin for PDP/reviews URLs, e.g. https://www.amazon.com"""
    host = normalize_amazon_domain(domain)
    if host.startswith("www."):
        return f"https://{host}"
    return f"https://www.{host}"


_AMAZON_HOST_TO_SCRAPER_COUNTRY: dict[str, str] = {
    "amazon.com": "us",
    "amazon.ca": "ca",
    "amazon.com.mx": "mx",
    "amazon.co.uk": "uk",
    "amazon.de": "de",
    "amazon.fr": "fr",
    "amazon.it": "it",
    "amazon.es": "es",
    "amazon.in": "in",
    "amazon.co.jp": "jp",
    "amazon.com.au": "au",
    "amazon.com.br": "br",
    "amazon.nl": "nl",
    "amazon.se": "se",
    "amazon.pl": "pl",
    "amazon.sg": "sg",
    "amazon.ae": "ae",
}


def resolve_scraperapi_country_code(explicit: str, amazon_domain: str) -> str | None:
    """
    ScraperAPI `country_code` for proxy geo (see ScraperAPI docs).
    Explicit SCRAPERAPI_COUNTRY_CODE wins; else inferred from AMAZON_DOMAIN host.
    """
    e = (explicit or "").strip().lower()
    if e:
        return e
    host = normalize_amazon_domain(amazon_domain)
    return _AMAZON_HOST_TO_SCRAPER_COUNTRY.get(host)


def extract_asin_from_amazon_url(url: str) -> str | None:
    import re

    url = url.strip()
    m = re.search(r"/(?:dp|gp/product|asin)/([A-Z0-9]{10})", url, flags=re.I)
    if m:
        return m.group(1).upper()
    m2 = re.search(r"\b(B0[A-Z0-9]{8})\b", url, flags=re.I)
    if m2:
        return m2.group(1).upper()
    return None
