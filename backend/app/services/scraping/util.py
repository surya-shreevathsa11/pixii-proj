import re
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup


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


def _canonicalize_url_for_parse(raw: str) -> tuple[str, ParseResult | None]:
    decoded = unquote(raw.strip())
    for_parsing = decoded.strip()
    if not re.match(r"^https?://", for_parsing, flags=re.I):
        for_parsing = "https://" + for_parsing.lstrip("/")
    try:
        parsed = urlparse(for_parsing)
    except ValueError:
        return for_parsing, None
    return for_parsing, parsed


def _amazon_host(host: str) -> bool:
    h = (host or "").lower()
    if not h:
        return False
    return (
        "amazon." in h
        or h.endswith("amzn.to")
        or h.endswith("amzn.in")
        or h == "a.co"
        or h.startswith("amzn.")
    )


def _needs_redirect_resolve(parsed: ParseResult | None, for_parsing: str) -> bool:
    """True for short domains (amzn.in) or /d/<non-ASIN> product slugs that only resolve after redirects."""
    if not parsed or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    if host in ("amzn.to", "amzn.in", "a.co", "www.amzn.to", "www.amzn.in"):
        return True
    if host.startswith("amzn."):
        return True
    path = (parsed.path or "").rstrip("/")
    m = re.match(r"^/d/([^/]+)$", path, flags=re.I)
    if m and _amazon_host(host):
        slug = m.group(1).strip().upper()
        if not _ASIN_FULL.match(slug):
            return True
    return False


def fetch_amazon_url_resolved(url: str, timeout: float = 15.0) -> tuple[str | None, str | None]:
    """
    GET with redirects. Returns (final_url, response_text) for parsing canonical/dp ASINs when the URL bar
    does not yet contain a standard /dp/ASIN path.
    """
    u = url.strip()
    if not u:
        return None, None
    if not re.match(r"^https?://", u, flags=re.I):
        u = "https://" + u.lstrip("/")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            rsp = client.get(u, headers=headers)
        rsp.raise_for_status()
        return str(rsp.url), rsp.text
    except Exception:
        return None, None


def _extract_asin_from_html(html: str) -> str | None:
    """Best-effort ASIN from PDP HTML (canonical, og:url, embedded /dp/ links)."""
    if not html or len(html) < 50:
        return None
    chunk = html[:800_000]
    soup = BeautifulSoup(chunk, "html.parser")
    for link in soup.select('link[rel="canonical"]'):
        href = (link.get("href") or "").strip()
        if href:
            got = _extract_asin_without_redirect(href)
            if got:
                return got
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.get("content"):
        got = _extract_asin_without_redirect(str(og["content"]).strip())
        if got:
            return got
    for m in re.finditer(r'["\']/dp/([A-Z0-9]{10})["\']', chunk, flags=re.I):
        got = _normalize_asin_token(m.group(1))
        if got:
            return got
    return None


def _extract_asin_without_redirect(url: str) -> str | None:
    """Parse ASIN from URL text only (no network)."""
    raw = (url or "").strip()
    if not raw:
        return None

    compact = re.sub(r"\s+", "", raw).upper()
    if _ASIN_FULL.match(compact):
        return compact

    decoded = unquote(raw)
    for_parsing, parsed = _canonicalize_url_for_parse(raw)

    haystack = decoded + " " + for_parsing
    if parsed and parsed.path:
        haystack = haystack + " " + parsed.path

    if parsed and parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=False)
        for key in ("asin", "ASIN", "creativeASIN"):
            vals = qs.get(key) or qs.get(key.lower())
            if vals:
                got = _normalize_asin_token(vals[0])
                if got:
                    return got

    path_patterns = [
        r"/(?:dp|gp/product|asin|gp/aw/d)/([A-Z0-9]{10})(?:[^\w]|$)",
        r"/d/([A-Z0-9]{10})(?:[^\w]|$)",
        r"/exec/obidos/(?:ASIN|ISBN)/([A-Z0-9]{10})(?:[^\w]|$)",
    ]
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

    for m in re.finditer(r"\b(B[A-Z0-9]{9})\b", haystack, flags=re.I):
        got = _normalize_asin_token(m.group(1))
        if got:
            return got

    for m in re.finditer(r"(?<![A-Z0-9])([A-Z0-9]{10})(?![A-Z0-9])", haystack.upper()):
        got = _normalize_asin_token(m.group(1))
        if got and got.startswith("B"):
            return got

    return None


def extract_asin_from_amazon_url(url: str) -> str | None:
    """
    Resolve a 10-character ASIN from an Amazon URL, bare ASIN, or common short forms.
    Follows redirects for amzn.in / amzn.to / a.co and for amazon.*/d/<slug> when the slug is not an ASIN.
    If the final URL still has no ASIN, parses the response HTML for canonical / og:url / /dp/ links.
    """
    got = _extract_asin_without_redirect(url)
    if got:
        return got

    for_parsing, parsed = _canonicalize_url_for_parse(url)
    if not _needs_redirect_resolve(parsed, for_parsing):
        return None

    final_url, body = fetch_amazon_url_resolved(for_parsing)
    if final_url:
        got = _extract_asin_without_redirect(final_url)
        if got:
            return got
    if body:
        got = _extract_asin_from_html(body)
        if got:
            return got

    return None
