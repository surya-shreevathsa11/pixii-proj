import re
from urllib.parse import parse_qs, unquote, urlparse


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


_ASIN_FULL = re.compile(r"^[A-Z0-9]{10}$")


def _normalize_asin_token(token: str) -> str | None:
    t = token.strip().upper()
    if _ASIN_FULL.match(t):
        return t
    return None


def extract_asin_from_amazon_url(url: str) -> str | None:
    """
    Resolve a 10-character ASIN from an Amazon URL, bare ASIN, or common short forms.
    """
    raw = (url or "").strip()
    if not raw:
        return None

    # Bare ASIN (possibly with spaces)
    compact = re.sub(r"\s+", "", raw).upper()
    if _ASIN_FULL.match(compact):
        return compact

    # Decode once for path-encoded URLs
    decoded = unquote(raw)

    # Ensure urlparse sees a netloc when user omits scheme (e.g. www.amazon.com/dp/...)
    for_parsing = decoded.strip()
    if not re.match(r"^https?://", for_parsing, flags=re.I):
        for_parsing = "https://" + for_parsing.lstrip("/")

    try:
        parsed = urlparse(for_parsing)
    except ValueError:
        parsed = None

    haystack = decoded + " " + for_parsing

    if parsed and parsed.path:
        haystack = haystack + " " + parsed.path

    # Query string ?asin=B00... or feature=...&asin=...
    if parsed and parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=False)
        for key in ("asin", "ASIN", "creativeASIN"):
            vals = qs.get(key) or qs.get(key.lower())
            if vals:
                got = _normalize_asin_token(vals[0])
                if got:
                    return got

    # Path segments: /dp/, /gp/product/, /asin/, mobile /gp/aw/d/, legacy obidos
    path_patterns = [
        r"/(?:dp|gp/product|asin|gp/aw/d)/([A-Z0-9]{10})(?:[^\w]|$)",
        r"/exec/obidos/(?:ASIN|ISBN)/([A-Z0-9]{10})(?:[^\w]|$)",
    ]
    # asin= anywhere (sponsored / SPA links)
    m_q = re.search(r"(?:^|[?&])(?:asin|ASIN)=([A-Z0-9]{10})(?:[^\w]|$)", haystack, flags=re.I)
    if m_q:
        got = _normalize_asin_token(m_q.group(1))
        if got:
            return got

    for pat in path_patterns:
        m = re.search(pat, haystack, flags=re.I)
        if m:
            got = _normalize_asin_token(m.group(1))
            if got:
                return got

    # Generic 10-char alnum tokens starting with B (common Amazon ASIN family)
    for m in re.finditer(r"\b(B[A-Z0-9]{9})\b", haystack, flags=re.I):
        got = _normalize_asin_token(m.group(1))
        if got:
            return got

    # Any isolated 10-char alnum (last resort; avoids matching inside longer ids)
    for m in re.finditer(r"(?<![A-Z0-9])([A-Z0-9]{10})(?![A-Z0-9])", haystack.upper()):
        got = _normalize_asin_token(m.group(1))
        if got and got.startswith("B"):
            return got

    return None
