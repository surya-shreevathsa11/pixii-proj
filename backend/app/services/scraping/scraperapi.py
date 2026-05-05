"""
Amazon HTML ingestion via https://www.scraperapi.com/ proxy.

Markup varies by locale (amazon.com vs amazon.co.uk, etc.) and changes over time.
Use SCRAPERAPI_RENDER=true when the DOM is mostly client-rendered, set AMAZON_DOMAIN
to match the storefront, and enable SCRAPERAPI_SAVE_HTML_ON_EMPTY to capture HTML
samples for tightening selectors locally.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from app.services.scraping.base import NormalizedListing, NormalizedReview
from app.services.scraping.util import (
    amazon_site_origin,
    normalize_amazon_domain,
    resolve_scraperapi_country_code,
)

logger = logging.getLogger(__name__)

# Competitor discovery: drop tiles that look like accessories/services when the PDP is a handset-class product.
_ACCESSORY_TILE_HINT = re.compile(
    r"\b(adapter|chargers?|charging\s+cables?|usb[\s-]?c\b|wall\s+plug|power\s+bricks?|"
    r"apple\s*20w|20\s*w\b|otg\b|\bhubs?\b|\bdocks?\b|"
    r"applecare|care\s*\+|protection\s+plans?|protection\s+plan\s+for|"
    r"damage\s+protection|screen\s+damage|extended\s+warrant|warranty\s+service|"
    r"\binsurance\b|service\s+plan|onsitego|acko\b|onsite\s+plan|breakage\s+plan|"
    r"\bcases?\b|\bcovers?\b|flip\s+cover|bumper|skins?|tempered\s+glass|screen\s+guards?|protectors?\b|"
    r"magnetic\s+wallet|silicone\s+case|earbuds?|airpods)\b",
    re.I,
)
_HANDSET_PRIMARY_HINT = re.compile(
    r"\b(iphone|pixel\s+\d|galaxy\s+s\d|galaxy\s+z\s|galaxy\s+a\d|oneplus|xiaomi|redmi|poco|nothing\s+phone|"
    r"oppo\s+reno|realme|vivo|motorola|smartphone|mobile\s+phones?|5g\s+phone)\b",
    re.I,
)
# Category-based service/warranty filter (applied after PDP fetch, when bsr_category / product_category is known).
_SERVICE_CATEGORY_HINT = re.compile(
    r"\b(warrant(?:y|ies)|insurance|service\s+plan|protection\s+plan|breakage\s+plan|"
    r"subscriptions?|memberships?|gift\s+cards?|software\s+downloads?|digital\s+services?)\b",
    re.I,
)

# Universal "this is a service/plan/warranty/subscription, not a physical product" filter.
# Applied to ALL competitive flows regardless of primary product type — a customer comparing
# laptops, headphones, blenders, anything, never wants warranty plans in the leaderboard.
_UNIVERSAL_SERVICE_TITLE_HINT = re.compile(
    r"\b(applecare|care\s*\+|protection\s+plan|damage\s+protection|screen\s+damage|"
    r"extended\s+warrant|warranty\s+service|\binsurance\b|service\s+plan|"
    r"onsitego|acko\b|onsite\s+plan|breakage\s+plan|"
    r"subscription|membership|gift\s+card|software\s+download|"
    r"installation\s+service|annual\s+maintenance)\b",
    re.I,
)

# Stopwords stripped from category leaves before computing overlap between primary & competitor.
_CATEGORY_STOPWORDS: frozenset[str] = frozenset({
    "amazon", "the", "and", "for", "with", "in", "of", "a", "an",
    "electronics", "home", "kitchen", "products", "store", "stores",
    "best", "sellers", "new", "releases", "all", "see", "more",
    "&", ">", "/", "|", "-",
})


def _category_leaf_tokens(category: str | None) -> set[str]:
    """Extract a normalized token set from the leaf-most segment of a breadcrumb-like category string."""
    if not category:
        return set()
    segments = re.split(r"\s*(?:>|/|\|)\s*", category)
    leaf = (segments[-1] if segments else category).lower()
    raw_tokens = re.findall(r"[a-z0-9]+", leaf)
    return {t for t in raw_tokens if len(t) > 2 and t not in _CATEGORY_STOPWORDS}


# Color / finish words removed when computing a product fingerprint. Kept narrow on purpose:
# words like "pro", "plus", "max", "mini" are MODEL qualifiers and stay in the fingerprint.
_VARIANT_COLOR_WORDS: frozenset[str] = frozenset({
    "black", "white", "silver", "gold", "rose", "pink", "purple", "violet",
    "red", "blue", "navy", "teal", "green", "olive", "yellow", "orange",
    "grey", "gray", "graphite", "charcoal", "midnight", "starlight",
    "aquamarine", "glacier", "stellar", "space", "titanium", "natural",
    "deep", "bronze", "champagne", "mint", "cream", "ivory", "beige",
    "sand", "coral", "lavender", "indigo", "ocean", "sky", "pearl",
    "obsidian", "onyx", "sapphire", "ruby", "emerald", "amber",
    "lime", "magenta", "crimson", "burgundy", "phantom", "frost",
    "crystal", "metallic", "matte", "glossy", "satin", "smooth",
    "color", "colour", "edition",
})

# Capacity / quantity tokens that vary across SKUs of the same product family.
_VARIANT_CAPACITY_RX = re.compile(
    r"\b("
    r"\d+\s?(?:gb|tb|mb)|"
    r"\d+\s?(?:ml|ltr|liter|litre|l)\b|"
    r"\d+\s?(?:mah|wh|w)\b|"
    r"\d+\s?(?:gm|gms|g|kg|kgs|oz|lb|lbs)\b|"
    r"\d+\s?(?:inch|inches|in|cm|mm|ft)\b|"
    r"\d+\s?ram|"
    r"\d+\s?storage|"
    r"\d+\s?pack|pack\s+of\s+\d+|combo\s+of\s+\d+"
    r")\b",
    re.I,
)

# Punctuation/separators that we collapse to spaces before tokenizing.
_FINGERPRINT_SEP_RX = re.compile(r"[|,/:_\-]+")
# Bracketed segments commonly carry color/storage/SKU data.
_BRACKET_RX = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
# Tokens that should never form part of a fingerprint (filler / marketing words).
_FINGERPRINT_STOPWORDS: frozenset[str] = frozenset({
    "amazon", "the", "and", "for", "with", "in", "of", "a", "an", "to",
    "new", "latest", "model", "version", "edition", "series",
    "india", "indias", "biggest", "ever", "best", "premium",
    "free", "delivery", "shipping", "official", "genuine",
    "smartphone", "smartphones", "phone", "phones", "mobile", "mobiles",
    "laptop", "laptops", "headphone", "headphones", "earphone", "earphones",
    "earbuds", "watch", "watches", "tablet", "tablets",
})


def _title_fingerprint(title: str | None) -> str:
    """Produce a canonical brand+model fingerprint used to collapse variant SKUs.

    Universal across product categories: removes bracketed SKU data, color/finish words,
    capacity tokens (storage/RAM/battery/volume/weight/length), and generic filler
    words. Keeps the first ~6 meaningful tokens which typically encode brand + model
    + key qualifier (e.g. iqoo z10 5g, dell xps 15, sony wh1000xm5).
    """
    if not title:
        return ""
    text = title.lower()
    text = _BRACKET_RX.sub(" ", text)
    text = _VARIANT_CAPACITY_RX.sub(" ", text)
    text = _FINGERPRINT_SEP_RX.sub(" ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    tokens = [t for t in text.split() if t]
    cleaned: list[str] = []
    for tok in tokens:
        if tok in _FINGERPRINT_STOPWORDS:
            continue
        if tok in _VARIANT_COLOR_WORDS:
            continue
        # Drop pure-digit junk that is neither year nor 4G/5G band.
        if tok.isdigit() and len(tok) > 4:
            continue
        cleaned.append(tok)
        if len(cleaned) >= 6:
            break
    return " ".join(cleaned)
_REVIEW_TITLE_STAR_PREFIX = re.compile(r"^\s*[\d.]+\s*out\s+of\s*5\s*stars\s*", re.I)


class ScraperApiScrapingProvider:
    """https://docs.scraperapi.com/making-requests/http-get-requests-get"""

    BASE = "https://api.scraperapi.com"

    def __init__(
        self,
        api_key: str,
        fallback_api_key: str = "",
        timeout: float = 120.0,
        render_timeout: float = 300.0,
        render: bool = False,
        country_code: Optional[str] = None,
        save_html_on_empty: bool = False,
    ):
        self.api_key = api_key
        keys = [api_key.strip(), fallback_api_key.strip()]
        deduped: list[str] = []
        for key in keys:
            if key and key not in deduped:
                deduped.append(key)
        self.api_keys = deduped
        self.timeout = timeout
        self.render_timeout = max(timeout, render_timeout)
        self.render = render
        explicit_cc = (country_code or "").strip().lower()
        self.country_code = explicit_cc or None
        self._explicit_country = bool(explicit_cc)
        self.save_html_on_empty = save_html_on_empty
        self._debug_dir = Path(__file__).resolve().parents[3] / "var" / "debug_scraperapi"

    def _country_for(self, amazon_domain: Optional[str]) -> Optional[str]:
        """Use explicit country if set; otherwise infer from the storefront domain (per call)."""
        if self._explicit_country and self.country_code:
            return self.country_code
        if amazon_domain:
            return resolve_scraperapi_country_code("", amazon_domain)
        return self.country_code

    def _canonical_target_url(self, url: str) -> str:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def _dump_debug_html(self, kind: str, slug: str, html: str, note: str) -> None:
        if not self.save_html_on_empty:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe = re.sub(r"[^\w.-]+", "_", slug)[:120]
            path = self._debug_dir / f"{kind}_{safe}_{ts}.html"
            banner = f"<!-- scraperapi debug {note} -->\n"
            path.write_text(banner + html, encoding="utf-8", errors="replace")
            logger.info("Saved ScraperAPI debug HTML to %s", path)
        except OSError as exc:
            logger.warning("Could not save debug HTML: %s", exc)

    async def _fetch_html(self, target_url: str, amazon_domain: Optional[str] = None) -> tuple[str, str]:
        canonical, body, _status = await self._fetch_html_lenient(target_url, amazon_domain, raise_on_error=True)
        return canonical, body

    async def _fetch_html_lenient(
        self,
        target_url: str,
        amazon_domain: Optional[str] = None,
        raise_on_error: bool = False,
        *,
        force_render: bool = False,
    ) -> tuple[str, str, int]:
        """Fetch a page through ScraperAPI without raising on 4xx by default.

        Returns (canonical_target_url, decoded_body, http_status). Callers can branch on
        ``status`` to fall back to alternate review strategies when Amazon 404s.
        """
        canonical = self._canonical_target_url(target_url)
        if not self.api_keys:
            if raise_on_error:
                raise ValueError("No ScraperAPI keys configured")
            return canonical, "", 401
        # ScraperAPI: every API flag must appear *before* `url` in the query string, or routing can break (often 404).
        # See https://docs.scraperapi.com/synchronous-apis/using-the-api-endpoint
        uses_render = bool(self.render or force_render)
        read_timeout = self.render_timeout if uses_render else self.timeout
        # Separate caps: connect can stay short; read must be generous for ScraperAPI + JS render queues.
        httpx_timeout = httpx.Timeout(read_timeout, connect=30.0, write=read_timeout, pool=30.0)
        last_net_exc: BaseException | None = None
        auth_like_failures = {401, 403, 429}
        country = self._country_for(amazon_domain)
        # ScraperAPI returns 499 when the upstream scrape failed (e.g. blocked / proxy churn) and 5xx for
        # internal errors. Both are transient; retry the GET a few times before giving up.
        TRANSIENT_STATUSES = {429, 499, 500, 502, 503, 504, 520, 522, 524}
        rsp: httpx.Response | None = None
        for key_idx, key in enumerate(self.api_keys):
            query_pairs: list[tuple[str, str]] = [("api_key", key)]
            if self.render or force_render:
                query_pairs.append(("render", "true"))
            if country:
                query_pairs.append(("country_code", country))
            query_pairs.append(("url", canonical))

            rsp = None
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=httpx_timeout, follow_redirects=True) as client:
                        rsp = await client.get(self.BASE, params=query_pairs)
                except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                    last_net_exc = exc
                    logger.warning(
                        "ScraperAPI GET timeout/connection issue (attempt %s/3, render=%s, read_timeout=%ss): %s",
                        attempt + 1,
                        uses_render,
                        read_timeout,
                        exc,
                    )
                    if attempt < 2:
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                    rsp = None
                    break

                # Got a response; retry transient upstream failures (ScraperAPI's 499 + 5xx) before bailing.
                if rsp.status_code in TRANSIENT_STATUSES and attempt < 2:
                    logger.warning(
                        "ScraperAPI transient %s (attempt %s/3, render=%s) for %s; retrying.",
                        rsp.status_code, attempt + 1, uses_render, canonical[:120],
                    )
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                break

            if rsp is None:
                if key_idx < len(self.api_keys) - 1:
                    logger.warning("ScraperAPI primary key failed; trying fallback key for %s", canonical[:120])
                    continue
                if raise_on_error and last_net_exc:
                    raise
                logger.error(
                    "ScraperAPI gave up after retries for %s (render=%s): %s",
                    canonical[:120],
                    uses_render,
                    last_net_exc,
                )
                return canonical, "", 504

            if rsp.status_code in auth_like_failures and key_idx < len(self.api_keys) - 1:
                logger.warning(
                    "ScraperAPI key rejected with %s for %s; trying fallback key.",
                    rsp.status_code,
                    canonical[:120],
                )
                continue
            break

        assert rsp is not None

        if raise_on_error and rsp.status_code >= 400:
            # Surface a structured error but never propagate raw HTTPStatusError to the job runner;
            # callers handle (canonical, "", status) and decide whether to abort or fall back.
            logger.warning(
                "ScraperAPI %s for %s (render=%s); returning empty body to caller.",
                rsp.status_code, canonical[:160], uses_render,
            )
            return canonical, "", rsp.status_code

        ctype = rsp.headers.get("content-type") or ""

        encoding = (
            getattr(rsp, "charset_encoding", None)
            or rsp.encoding
            or "utf-8"
        )
        try:
            html = rsp.content.decode(encoding, errors="replace")
        except (LookupError, TypeError):
            html = rsp.content.decode("utf-8", errors="replace")

        if "application/json" in ctype.lower():
            try:
                import json

                data = json.loads(html)
                if isinstance(data, dict) and isinstance(data.get("body"), str):
                    html = data["body"]
            except json.JSONDecodeError:
                pass

        return canonical, html, rsp.status_code

    @staticmethod
    def _amazon_in_client_render_heavy(amazon_domain: str) -> bool:
        return normalize_amazon_domain(amazon_domain).lower().endswith("amazon.in")

    async def _fetch_structured_reviews(
        self,
        asin: str,
        amazon_domain: str,
        page: int,
        *,
        force_render: bool = False,
    ) -> tuple[list[NormalizedReview], Optional[str], int]:
        """Use ScraperAPI's structured Amazon Reviews endpoint as a fallback.

        Docs: https://docs.scraperapi.com/structured-data-collection-method/amazon/amazon-review
        Returns (reviews, next_token, status_code). Status 0 means the call itself failed.
        """
        domain = normalize_amazon_domain(amazon_domain)
        # tld is the part *after* "amazon." (e.g. "in", "co.uk", "com").
        tld = domain[len("amazon."):] if domain.startswith("amazon.") else "com"
        country = self._country_for(amazon_domain) or "us"

        if not self.api_keys:
            return [], None, 401

        uses_render = bool(self.render or force_render)
        url = "https://api.scraperapi.com/structured/amazon/review"
        read_timeout = self.render_timeout if uses_render else self.timeout
        httpx_timeout = httpx.Timeout(read_timeout, connect=30.0, write=read_timeout, pool=30.0)
        rsp: httpx.Response | None = None
        auth_like_failures = {401, 403, 429}
        for key_idx, key in enumerate(self.api_keys):
            params: list[tuple[str, str]] = [
                ("api_key", key),
                ("asin", asin.upper()),
                ("tld", tld),
                ("country", country),
                ("page", str(page)),
            ]
            if uses_render:
                params.append(("render", "true"))
            rsp = None
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=httpx_timeout, follow_redirects=True) as client:
                        rsp = await client.get(url, params=params)
                    break
                except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                    logger.warning(
                        "ScraperAPI structured reviews timeout (attempt %s/3, render=%s): %s p%s: %s",
                        attempt + 1,
                        uses_render,
                        asin,
                        page,
                        exc,
                    )
                    if attempt < 2:
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                    rsp = None
                    break
                except httpx.HTTPError as exc:
                    logger.warning("ScraperAPI structured reviews call failed for %s p%s: %s", asin, page, exc)
                    rsp = None
                    break

            if rsp is None:
                if key_idx < len(self.api_keys) - 1:
                    logger.warning("Structured reviews failed for primary key on %s p%s; trying fallback key", asin, page)
                    continue
                return [], None, 0

            if rsp.status_code in auth_like_failures and key_idx < len(self.api_keys) - 1:
                logger.warning(
                    "Structured reviews key rejected (%s) for %s p%s; trying fallback key.",
                    rsp.status_code,
                    asin,
                    page,
                )
                continue
            break

        if rsp is None:
            return [], None, 0

        if rsp.status_code >= 400:
            logger.info(
                "ScraperAPI structured reviews returned %s for %s p%s", rsp.status_code, asin, page,
            )
            return [], None, rsp.status_code

        try:
            data = rsp.json()
        except ValueError:
            logger.info("ScraperAPI structured reviews returned non-JSON for %s p%s", asin, page)
            return [], None, rsp.status_code

        reviews_raw: list[dict] = []
        if isinstance(data, dict):
            for key in ("reviews", "results", "data"):
                cand = data.get(key)
                if isinstance(cand, list):
                    reviews_raw = cand
                    break
            if not reviews_raw:
                for key in ("product_reviews", "customer_reviews", "review_list", "top_reviews"):
                    cand = data.get(key)
                    if isinstance(cand, list):
                        reviews_raw = cand
                        break
                    if isinstance(cand, dict):
                        inner = cand.get("reviews") or cand.get("items") or cand.get("results")
                        if isinstance(inner, list):
                            reviews_raw = inner
                            break

        out: list[NormalizedReview] = []
        for idx, item in enumerate(reviews_raw):
            if not isinstance(item, dict):
                continue
            ext_id = (
                str(item.get("id") or item.get("review_id") or item.get("reviewId") or "").strip()
                or f"{asin.upper()}-struct-{page}-{idx}"
            )
            rating: Optional[int] = None
            raw_rating = item.get("rating") or item.get("stars")
            if raw_rating is not None:
                try:
                    rating = max(1, min(5, int(round(float(raw_rating)))))
                except (TypeError, ValueError):
                    rating = None

            title = item.get("title") or item.get("review_title") or None
            if title:
                t2 = _REVIEW_TITLE_STAR_PREFIX.sub("", str(title)).strip()
                title = t2 if t2 else None
            body = item.get("body") or item.get("review") or item.get("text") or ""
            if isinstance(body, list):
                body = "\n".join(str(part) for part in body if part)
            body = str(body)[:8192]

            date = item.get("date") or item.get("review_date") or item.get("posted_on")
            date_str = str(date)[:32] if date else None

            verified_raw = item.get("verified") or item.get("verified_purchase") or item.get("is_verified")
            if isinstance(verified_raw, str):
                vp = verified_raw.strip().lower() in ("true", "yes", "1", "verified")
            else:
                vp = bool(verified_raw)

            images = item.get("images") or item.get("review_images") or item.get("photos")
            has_img = bool(images) if not isinstance(images, str) else bool(images.strip())

            if body.strip() or rating is not None or title:
                out.append(
                    NormalizedReview(
                        external_id=ext_id,
                        rating=rating,
                        title=str(title)[:512] if title else None,
                        body=body,
                        review_date=date_str,
                        is_verified_purchase=vp,
                        has_customer_images=has_img,
                    )
                )

        # Determine pagination heuristically: if we got >= 8 rows assume there's another page.
        next_token: Optional[str] = None
        if isinstance(data, dict):
            tp = data.get("total_pages") or data.get("totalPages")
            try:
                if tp and int(tp) > page:
                    next_token = str(page + 1)
            except (TypeError, ValueError):
                pass
        if next_token is None and len(out) >= 8:
            next_token = str(page + 1)

        return out, next_token, rsp.status_code

    def _looks_like_blocked(self, html: str) -> bool:
        low = html[:8000].lower()
        return any(
            k in low
            for k in (
                "robot check",
                "enter the characters you see below",
                "api error",
                "scraperapi reference",
                "invalid api key",
            )
        )

    @staticmethod
    def _currency_from_price_text(txt: str) -> str:
        t = txt.strip()
        if "£" in t or "gbp" in t.lower():
            return "GBP"
        if "€" in t or "eur" in t.lower():
            return "EUR"
        if "￥" in t or "¥" in t or "jpy" in t.lower():
            return "JPY"
        if "cdn" in t.lower() or "ca$" in t.lower() or "c $" in t.lower():
            return "CAD"
        if "₹" in t or "rs." in t.lower() or "rs " in t.lower() or "inr" in t.lower() or "rupees" in t.lower():
            return "INR"
        if "$" in t or "usd" in t.lower():
            return "USD"
        return "USD"

    @staticmethod
    def _default_currency_for_store(store_domain: Optional[str]) -> str:
        d = (store_domain or "").lower().strip()
        if "amazon.in" in d or d == "amazon.in":
            return "INR"
        if "amazon.co.uk" in d or "amazon.uk" in d:
            return "GBP"
        if "amazon.de" in d:
            return "EUR"
        if "amazon.fr" in d:
            return "EUR"
        if "amazon.it" in d:
            return "EUR"
        if "amazon.es" in d:
            return "EUR"
        if "amazon.co.jp" in d or "amazon.jp" in d:
            return "JPY"
        if "amazon.ca" in d:
            return "CAD"
        if "amazon.com.au" in d or "amazon.au" in d:
            return "AUD"
        if "amazon.ae" in d:
            return "AED"
        if "amazon.sg" in d:
            return "SGD"
        return "USD"

    @staticmethod
    def _parse_amount_string(raw: str) -> Optional[float]:
        s = raw.strip().replace("\xa0", " ")
        s = re.sub(r"\s+", "", s)
        if not s:
            return None
        if "," in s and "." in s:
            s = s.replace(",", "")
        elif "," in s and "." not in s:
            parts = s.split(",")
            if len(parts) == 2 and len(parts[1]) in (1, 2):
                s = parts[0] + "." + parts[1]
            else:
                # Thousands separators (US/IN style) or Indian grouping — strip commas.
                s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None

    @staticmethod
    def _looks_like_price_offscreen(text: str) -> bool:
        t = text.strip()
        if not t or len(t) > 56:
            return False
        low = t.lower()
        if re.search(r"out\s+of\s+5|stars?|answered\s+questions|visit\s+the|store|delivery|coupon|subscribe", low):
            return False
        if re.search(r"[₹$£€]|rs\.?\s*\d|inr|usd|gbp|eur", low):
            return True
        # Buy box sometimes exposes only digits inside a price subtree (symbol in sibling).
        if re.fullmatch(r"[\d,]+(?:\.\d{1,2})?", t) and len(t) <= 16:
            return True
        return False

    @classmethod
    def _parse_price_line(cls, line: str) -> tuple[Optional[float], str]:
        """Parse a single human-readable price line (e.g. '₹1,999.00' or '$19.99')."""
        txt = line.strip()
        if not txt:
            return None, cls._currency_from_price_text("")
        cur = cls._currency_from_price_text(txt)
        # Strip currency words/symbols then take the first substantial number run.
        cleaned = re.sub(r"[₹$£€]|rs\.?|inr|usd|gbp|eur", "", txt, flags=re.I)
        cleaned = cleaned.strip()
        m = re.search(r"([\d][\d,\.]*)", cleaned)
        if not m:
            return None, cur
        amt = cls._parse_amount_string(m.group(1))
        return amt, cur

    @classmethod
    def _price_from_offer_dict(cls, offer: dict) -> tuple[Optional[float], Optional[str]]:
        if not isinstance(offer, dict):
            return None, None
        otype = offer.get("@type")
        price = None
        if otype == "AggregateOffer":
            price = offer.get("lowPrice") or offer.get("highPrice") or offer.get("price")
        else:
            price = offer.get("price")
        if price is None:
            return None, None
        cur = offer.get("priceCurrency")
        try:
            val = float(str(price).replace(",", "").strip())
        except (TypeError, ValueError):
            return None, None
        if val <= 0:
            return None, None
        return val, (str(cur).upper() if cur else None)

    @classmethod
    def _extract_price_from_json_ld(cls, soup: BeautifulSoup) -> tuple[Optional[float], Optional[str]]:
        for script in soup.find_all("script", type=re.compile(r"application/ld\+json", re.I)):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            def walk(node) -> tuple[Optional[float], Optional[str]]:
                if isinstance(node, dict):
                    types = node.get("@type")
                    is_product = types == "Product" or (isinstance(types, list) and "Product" in types)
                    if is_product:
                        offers = node.get("offers")
                        if isinstance(offers, list):
                            for off in offers:
                                p, c = cls._price_from_offer_dict(off if isinstance(off, dict) else {})
                                if p is not None:
                                    return p, c
                        elif isinstance(offers, dict):
                            p, c = cls._price_from_offer_dict(offers)
                            if p is not None:
                                return p, c
                    if "@graph" in node:
                        for item in node.get("@graph") or []:
                            p, c = walk(item)
                            if p is not None:
                                return p, c
                elif isinstance(node, list):
                    for it in node:
                        p, c = walk(it)
                        if p is not None:
                            return p, c
                return None, None

            found = walk(data)
            if found[0] is not None:
                return found
        return None, None

    @classmethod
    def _extract_price_from_meta(cls, soup: BeautifulSoup) -> tuple[Optional[float], Optional[str]]:
        pairs = (
            ("og:price:amount", "og:price:currency"),
            ("product:price:amount", "product:price:currency"),
        )
        for amt_prop, cur_prop in pairs:
            am_el = soup.find("meta", property=amt_prop)
            if not am_el or not am_el.get("content"):
                continue
            amt = cls._parse_amount_string(am_el["content"])
            if amt is None or amt <= 0:
                continue
            cur_el = soup.find("meta", property=cur_prop)
            cur = (cur_el.get("content") or "").strip().upper() if cur_el else ""
            if not cur:
                cur = cls._currency_from_price_text(am_el.get("content", ""))
            return amt, cur or "USD"

        ip = soup.find("meta", attrs={"itemprop": "price"})
        if ip and ip.get("content"):
            amt = cls._parse_amount_string(ip["content"])
            if amt is not None and amt > 0:
                cur_el = soup.find("meta", attrs={"itemprop": "priceCurrency"})
                cur = (cur_el.get("content") or "").strip().upper() if cur_el else ""
                return amt, cur or "USD"
        return None, None

    @classmethod
    def _extract_price_from_embedded_json(
        cls, html: str, store_domain: Optional[str],
    ) -> tuple[Optional[float], Optional[str]]:
        """Best-effort parse of common Amazon client-render price blobs (esp. .in mobile / twister)."""
        default_cur = cls._default_currency_for_store(store_domain)
        # "priceToPay":{"moneyValue":{"amount":1999.0,...},"displayString":"₹1,999.00"}
        m = re.search(
            r'"priceToPay"\s*:\s*\{[^}]*"moneyValue"\s*:\s*\{[^}]*"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            html,
        )
        if m:
            try:
                val = float(m.group(1))
                if val > 0:
                    window = html[max(0, m.start() - 500) : m.start() + 300]
                    cur = default_cur
                    if "₹" in window or "Rs." in window or "INR" in window.upper():
                        cur = "INR"
                    elif "$" in window and default_cur == "USD":
                        cur = "USD"
                    return val, cur
            except ValueError:
                pass
        return None, None

    _BUYBOX_ROOT_SELECTORS: tuple[str, ...] = (
        "#reinventPrice_feature_div",
        ".reinventPricePriceToPayMargin",
        "#corePrice_feature_div",
        "#corePriceDisplay_desktop_feature_div",
        "#corePriceDisplay_mobile_feature_div",
        "#apex_desktop_desktopDisplayPrice",
        "#apex_desktop",
        "#apex_offerDisplay_desktop",
        "#unifiedPrice_feature_div",
        "#desktop_buybox",
        "#buybox",
        "#snsAccordionRowMiddle",
        "#twister-plus-price-data-price",
    )

    def _extract_price_from_buybox(self, soup: BeautifulSoup, default_cur: str) -> tuple[Optional[float], str]:
        """Prefer visible buy-box prices; avoids carousel / compare-at / sponsored tiles."""
        for sel in self._BUYBOX_ROOT_SELECTORS:
            root = soup.select_one(sel)
            if not root:
                continue
            # Full price in one offscreen span inside a-price (most accurate on .in)
            for off in root.select(".a-price:not(.a-text-price) .a-offscreen"):
                txt = off.get_text(strip=True)
                if not self._looks_like_price_offscreen(txt):
                    continue
                amt, cur = self._parse_price_line(txt)
                if amt is not None and amt > 0:
                    if cur == "USD" and default_cur == "INR" and "₹" in root.get_text(" ", strip=True):
                        cur = "INR"
                    elif cur == "USD" and default_cur != "USD":
                        cur = default_cur
                    return amt, cur

            whole_nodes = root.select(".a-price:not(.a-text-price) .a-price-whole")
            frac_nodes = root.select(".a-price:not(.a-text-price) .a-price-fraction")
            if whole_nodes:
                integral = "".join(ch for ch in whole_nodes[0].get_text() if ch.isdigit())
                fr = frac_nodes[0].get_text(strip=True) if frac_nodes else ""
                fr_digits = ("".join(ch for ch in fr if ch.isdigit()) + "00")[:2]
                if integral:
                    try:
                        amt = float(f"{integral}.{fr_digits}")
                        p = whole_nodes[0].find_parent(class_=re.compile("a-price"))
                        parent_txt = p.get_text(" ", strip=True) if p else ""
                        cur = self._currency_from_price_text(parent_txt or whole_nodes[0].get_text(" ", strip=True))
                        if cur == "USD" and default_cur == "INR" and "₹" in root.get_text(" ", strip=True):
                            cur = "INR"
                        elif cur == "USD" and default_cur != "USD":
                            cur = default_cur
                        if amt > 0:
                            return amt, cur
                    except ValueError:
                        pass
        return None, ""

    def _extract_price_currency(
        self,
        soup: BeautifulSoup,
        html_snippet: str,
        store_domain: Optional[str],
    ) -> tuple[Optional[float], str, str]:
        """Return (price, currency, source_tag) for telemetry."""
        default_cur = self._default_currency_for_store(store_domain)

        p, c = self._extract_price_from_json_ld(soup)
        if p is not None and p > 0:
            return p, c or default_cur, "json_ld"

        p, c = self._extract_price_from_meta(soup)
        if p is not None and p > 0:
            return p, c or default_cur, "meta"

        p, c = self._extract_price_from_embedded_json(html_snippet, store_domain)
        if p is not None and p > 0:
            return p, c or default_cur, "embedded_json"

        p, c = self._extract_price_from_buybox(soup, default_cur)
        if p is not None and p > 0:
            if c == "USD" and default_cur == "INR":
                dp = soup.select_one("#dp, #buybox")
                if dp and "₹" in dp.get_text(" ", strip=True):
                    c = "INR"
            elif c == "USD" and default_cur != "USD":
                c = default_cur
            return p, c, "buybox_dom"

        # Last resort: scope to main #dp column only (reduces picking carousel prices).
        dp = soup.select_one("#dp, #dp-container, main#main")
        search_roots = [dp] if dp else [soup]
        for root in search_roots:
            if root is None:
                continue
            for sel in (
                ".a-price[data-a-color=\"price\"] .a-offscreen",
                ".a-price:not(.a-text-price) .a-offscreen",
            ):
                for off in root.select(sel):
                    txt = off.get_text(strip=True)
                    if not self._looks_like_price_offscreen(txt):
                        continue
                    amt, cur = self._parse_price_line(txt)
                    if amt is not None and amt > 0:
                        if cur == "USD" and default_cur != "USD":
                            cur = default_cur
                        return amt, cur, "dp_scoped_offscreen"

            whole_sels = root.select(".a-price:not(.a-text-price) .a-price-whole")
            frac_sels = root.select(".a-price:not(.a-text-price) .a-price-fraction")
            if whole_sels:
                integral = "".join(ch for ch in whole_sels[0].get_text() if ch.isdigit())
                fr = frac_sels[0].get_text(strip=True) if frac_sels else ""
                fr_digits = ("".join(ch for ch in fr if ch.isdigit()) + "00")[:2]
                if integral:
                    try:
                        amt = float(f"{integral}.{fr_digits}")
                        p = whole_sels[0].find_parent(class_=re.compile("a-price"))
                        parent_txt = p.get_text(" ", strip=True) if p else ""
                        cur = self._currency_from_price_text(parent_txt or whole_sels[0].get_text(" ", strip=True))
                        if cur == "USD" and default_cur != "USD":
                            cur = default_cur
                        if amt > 0:
                            return amt, cur, "dp_scoped_whole"
                    except ValueError:
                        pass

        return None, default_cur, "none"

    def _collect_detail_text(self, soup: BeautifulSoup) -> str:
        selectors = (
            "#detailBullets_feature_div",
            "#detailBulletsWrapper_feature_div",
            "#productDetails_feature_div",
            "#productDetails_placement_1",
            "#prodDetails",
            "#productDetails_detailBullets_sections1",
            "#feature-bullets",
            "table#product-specification-table",
            "#tech",
            ".a-expander-content",
        )
        chunks: list[str] = []
        for sel in selectors:
            for node in soup.select(sel):
                t = node.get_text("\n", strip=True)
                if t and len(t) > 20:
                    chunks.append(t)
        return "\n".join(chunks[:120])

    def _extract_bsr(self, soup: BeautifulSoup, html_fallback: str) -> tuple[Optional[int], Optional[str]]:
        mega = self._collect_detail_text(soup)
        if len(mega) < 80:
            mega = (mega + "\n" + html_fallback)[:200_000]

        patterns = [
            r"Best\s+Sellers\s+Rank[^\n#]*?#([\d,.]+(?:,\d{3})*)\s+(?:in|In)\s+([^\n(#<]{3,160})",
            r"Amazon\s+Best\s*Sellers\s+Rank[^\n#]*?#([\d,.]+(?:,\d{3})*)\s+(?:in|In)\s+([^\n(#<]{3,160})",
            # amazon.in and other locales: looser digit groups (e.g. Indian grouping).
            r"Best\s+Sellers\s+Rank[^\n#]*?#\s*([\d,]+)\s+(?:in|In)\s+([^\n(#<]{3,160})",
            r"Classement\s+des\s+meilleures\s+ventes[^\n#]*?n[°o]\s*([\d\s]+)\s+en\s+([^(\n<#]{3,160})",
            r"#([\d,.]+(?:,\d{3})*)\s+in\s+([A-Za-zÀ-ÿ0-9 &,'\-]{3,140})",
            r"#\s*([\d,]+)\s+in\s+([A-Za-zÀ-ÿ0-9 &,'\-]{3,140})",
        ]
        for pat in patterns:
            m = re.search(pat, mega, re.I | re.S)
            if m:
                digits = re.sub(r"[^\d]", "", m.group(1))
                if not digits:
                    continue
                try:
                    rank = int(digits)
                except ValueError:
                    continue
                cat = re.sub(r"\s+", " ", m.group(2).strip())
                cat = re.sub(r"\s*\(.*$", "", cat).strip()
                cat = cat.rstrip(" ,")[:512]
                return rank, cat or None
        return None, None

    _BREADCRUMB_SKIP = frozenset(
        {
            "amazon",
            "amazon.com",
            "amazon.in",
            "home",
            "shop",
            "today's deals",
            "todays deals",
            "best sellers",
            "gift cards",
        }
    )

    def _jsonld_objects(self, data: Any) -> list[dict]:
        out: list[dict] = []
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        out.append(item)
            else:
                out.append(data)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    out.append(item)
        return out

    def _extract_jsonld_product_category(self, soup: BeautifulSoup) -> Optional[str]:
        for script in soup.select('script[type="application/ld+json"]'):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for obj in self._jsonld_objects(data):
                types = obj.get("@type") or obj.get("type")
                if isinstance(types, list):
                    type_set = {str(t).lower() for t in types}
                elif types:
                    type_set = {str(types).lower()}
                else:
                    type_set = set()
                if not type_set & {"product", "http://schema.org/product", "https://schema.org/product"}:
                    continue
                cat = obj.get("category") or obj.get("productCategory")
                if isinstance(cat, str) and len(cat.strip()) > 2:
                    return cat.strip()[:512]
                if isinstance(cat, list) and cat:
                    joined = " › ".join(str(c).strip() for c in cat if str(c).strip())
                    if joined:
                        return joined[:512]
        return None

    def _extract_browse_category(self, soup: BeautifulSoup) -> Optional[str]:
        segments: list[str] = []
        root = soup.select_one("#wayfinding-breadcrumbs_feature_div")
        if not root:
            root = soup.select_one("ul.a-unordered-list.a-horizontal.a-size-small")
        if root:
            for a in root.select("a.a-link-normal[href]"):
                t = a.get_text(" ", strip=True)
                if not t or len(t) < 2:
                    continue
                low = t.casefold().strip()
                if low in self._BREADCRUMB_SKIP:
                    continue
                segments.append(t)
        if len(segments) < 2:
            nav = soup.select_one('nav[role="navigation"]')
            if nav:
                for a in nav.select("a[href*='node=']"):
                    t = a.get_text(" ", strip=True)
                    if not t or len(t) < 2:
                        continue
                    low = t.casefold().strip()
                    if low in self._BREADCRUMB_SKIP:
                        continue
                    segments.append(t)
        if len(segments) < 2:
            return None
        tail = segments[-4:]
        joined = " › ".join(tail)
        return joined[:512] if joined else None

    def _extract_title(self, soup: BeautifulSoup) -> str:
        selectors = (
            "#productTitle",
            "#title",
            "h1#title",
            'h1.a-size-large[data-automation-id="title"]',
            '[data-hook="product-title"]',
            'a[data-hook="product-title-link"]',
            "span#productTitle",
        )
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t and len(t) > 3:
                    return t
        og = soup.select_one("meta[property='og:title']")
        if og and og.get("content"):
            c = str(og["content"]).strip()
            if c and len(c) > 16 and not c.lower().startswith("amazon.com:"):
                return c
        return ""

    def _extract_avg_rating(self, soup: BeautifulSoup) -> Optional[float]:
        selectors = (
            'span[data-hook="rating-out-of-text"]',
            "#acrPopover",
            "span.a-icon-alt",
            "#averageCustomerReviews span.a-size-base",
            "i[data-hook='average-star-rating'] + span",
        )
        for sel in selectors:
            el = soup.select_one(sel)
            if not el:
                continue
            blob = (el.get("aria-label") or "") + " " + el.get_text(" ", strip=True)
            m = re.search(r"([\d,.]+)\s*(?:out\s+of|/|von|sur)\s*5", blob, flags=re.I)
            if not m:
                m = re.search(r"([\d,.]+)\s*/\s*5", blob)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except ValueError:
                    continue
        return None

    def _extract_review_count(self, soup: BeautifulSoup) -> Optional[int]:
        selectors = (
            "#acrCustomerReviewText",
            'span[data-hook="total-review-count"]',
            "#acrCustomerReviewLink",
            'a#acrCustomerReviewLink span[id="acrCustomerReviewText"]',
            'span[data-hook="acr-average-stars-rating-text"]',
        )
        for sel in selectors:
            el = soup.select_one(sel)
            if not el:
                continue
            txt = el.get_text(" ", strip=True)
            m2 = re.search(r"([\d][\d,\.]*)", txt.replace(",", ""))
            if m2:
                try:
                    digits = re.sub(r"[^\d]", "", m2.group(1))
                    if digits:
                        return int(digits)
                except ValueError:
                    continue
        return None

    # Matches "10K+ bought in past month", "1,000+ bought in past month", "1.5M bought in past month".
    _BOUGHT_RE = re.compile(
        r"([\d][\d,\.]*)\s*([KkMm])?\s*\+?\s*bought\s+in\s+past\s+month",
        re.I,
    )

    @classmethod
    def _extract_bought_past_month(cls, soup: BeautifulSoup) -> tuple[Optional[int], Optional[str]]:
        """Parse the "X bought in past month" social-proof badge from a PDP.

        Returns (units_lower_bound, raw_label). When the badge says "10K+", we treat
        10_000 as a lower-bound estimate. None when the badge isn't shown.
        """
        candidates: list[str] = []
        for sel in (
            "#social-proofing-faceout-title-tk_bought",
            "#socialProofingAsinFaceout_feature_div",
            "#socialProofingAsinFaceout",
            '[data-feature-name="socialProofingAsinFaceout"]',
            "#acrCustomerReviewText + span",
            "div.social-proofing-faceout",
        ):
            for el in soup.select(sel):
                txt = el.get_text(" ", strip=True)
                if txt:
                    candidates.append(txt)

        # Fallback: scan the full document (cheap, regex is anchored to the badge phrase).
        if not candidates:
            doc_text = soup.get_text(" ", strip=True)
            if "bought in past month" in doc_text.lower():
                candidates.append(doc_text)

        for txt in candidates:
            m = cls._BOUGHT_RE.search(txt)
            if not m:
                continue
            number_raw = m.group(1).replace(",", "").strip()
            suffix = (m.group(2) or "").lower()
            try:
                base = float(number_raw)
            except ValueError:
                continue
            multiplier = 1
            if suffix == "k":
                multiplier = 1_000
            elif suffix == "m":
                multiplier = 1_000_000
            units = int(base * multiplier)
            if units <= 0:
                continue
            label_match = re.search(
                r"[\d][\d,\.]*\s*[KkMm]?\+?\s*bought\s+in\s+past\s+month", txt, re.I,
            )
            label = label_match.group(0).strip() if label_match else None
            return units, label
        return None, None

    def _listing_from_soup(
        self,
        asin: str,
        soup: BeautifulSoup,
        canonical: str,
        raw_html_len: int,
        store_domain: Optional[str] = None,
    ) -> NormalizedListing:
        html_blob = soup.decode()

        title_raw = self._extract_title(soup)
        title = title_raw or f"Amazon product {asin}"

        avg = self._extract_avg_rating(soup)
        review_count = self._extract_review_count(soup)

        price, currency, price_source = self._extract_price_currency(
            soup, html_blob[:500_000], store_domain,
        )
        bsr_rank, bsr_cat = self._extract_bsr(soup, html_blob[:200_000])
        prev_units, prev_label = self._extract_bought_past_month(soup)
        browse_cat = self._extract_browse_category(soup)
        jsonld_cat = self._extract_jsonld_product_category(soup)
        product_category = browse_cat or jsonld_cat

        thin_parse = (not title_raw.strip()) or (price is None and bsr_rank is None)

        warnings: list[str] = []
        if thin_parse and not self.render:
            warnings.append(
                "Parse looks thin (missing title and/or price+BSR). Try SCRAPERAPI_RENDER=true, "
                "confirm AMAZON_DOMAIN matches the storefront, or enable SCRAPERAPI_SAVE_HTML_ON_EMPTY to capture HTML."
            )

        for msg in warnings:
            logger.warning("ScraperAPI %s (%s): %s", asin, canonical, msg)

        return NormalizedListing(
            asin=asin.upper(),
            title=title,
            price=price,
            currency=currency,
            bsr_rank=bsr_rank,
            bsr_category=bsr_cat,
            avg_rating=avg,
            review_count=review_count,
            canonical_url=canonical,
            previous_month_units=prev_units,
            previous_month_label=prev_label,
            product_category=product_category,
            raw={
                "provider": "scraperapi",
                "html_chars": raw_html_len,
                "canonical_url": canonical,
                "parse_thin": thin_parse,
                "render": self.render,
                "country_code": self.country_code,
                "warnings": warnings,
                "previous_month_label": prev_label,
                "price_source": price_source,
                "store_domain_hint": store_domain or "",
                "browse_category": browse_cat,
                "jsonld_category": jsonld_cat,
            },
        )

    @staticmethod
    def _is_thin_pdp_html(html: str) -> bool:
        """Quick test: does this HTML look like a real Amazon PDP we can scrape?

        A 4xx response, an empty body, or a stub that has none of the canonical PDP
        markers indicates we should retry with render=true (or give up) rather than
        fall through to ``_listing_from_soup`` and emit a placeholder ``Amazon product``
        row with all-N/A fields.
        """
        if not html or len(html) < 4000:
            return True
        low = html.lower()
        markers = (
            "id=\"producttitle\"",
            "id='producttitle'",
            "data-feature-name=\"title\"",
            "property=\"og:title\"",
            "property='og:title'",
        )
        return not any(m in low for m in markers)

    async def fetch_listing(self, asin: str, amazon_domain: str) -> NormalizedListing:
        site = amazon_site_origin(amazon_domain)
        target = f"{site}/dp/{asin.upper()}"

        # First attempt: respect SCRAPERAPI_RENDER (cheap path when off).
        canonical, html, status = await self._fetch_html_lenient(target, amazon_domain)

        # If the first pass came back empty / 4xx / a non-PDP shell, retry once with
        # render=true. This covers ScraperAPI 4xx → empty body and Amazon stubs that
        # don't include #productTitle until JS executes (common on amazon.in).
        if (status >= 400 or self._is_thin_pdp_html(html)) and not self.render:
            canonical_r, html_r, status_r = await self._fetch_html_lenient(
                target, amazon_domain, force_render=True,
            )
            if status_r < 400 and html_r and not self._is_thin_pdp_html(html_r):
                canonical, html, status = canonical_r, html_r, status_r

        if self._looks_like_blocked(html):
            return NormalizedListing(
                asin=asin.upper(),
                title=f"(blocked?) {asin}",
                price=None,
                currency="USD",
                bsr_rank=None,
                bsr_category=None,
                avg_rating=None,
                review_count=None,
                canonical_url=canonical,
                previous_month_units=None,
                previous_month_label=None,
                product_category=None,
                raw={
                    "provider": "scraperapi",
                    "error": "block_or_challenge",
                    "parse_thin": True,
                    "html_sample": html[:500],
                },
            )

        # Empty body / non-PDP shell after retry: surface a thin listing the runner can drop,
        # rather than the friendly "Amazon product B0XXX" placeholder that pollutes the report.
        if not html or self._is_thin_pdp_html(html):
            return NormalizedListing(
                asin=asin.upper(),
                title="",
                price=None,
                currency="",
                bsr_rank=None,
                bsr_category=None,
                avg_rating=None,
                review_count=None,
                canonical_url=canonical,
                previous_month_units=None,
                previous_month_label=None,
                product_category=None,
                raw={
                    "provider": "scraperapi",
                    "parse_thin": True,
                    "http_status": status,
                    "amazon_domain_hint": normalize_amazon_domain(amazon_domain),
                },
            )

        soup = BeautifulSoup(html, "html.parser")
        listing = self._listing_from_soup(
            asin.upper(), soup, canonical, len(html), normalize_amazon_domain(amazon_domain),
        )

        raw = dict(listing.raw) if isinstance(listing.raw, dict) else {}
        raw["amazon_domain_hint"] = normalize_amazon_domain(amazon_domain)
        listing.raw = raw

        if self.save_html_on_empty and raw.get("parse_thin"):
            self._dump_debug_html("listing", asin.upper(), html, "parse_thin")

        return listing

    @staticmethod
    def _asin_from_data_asin_node(node) -> Optional[str]:
        a = (node.get("data-asin") or "").strip().upper()
        if len(a) == 10 and a.isalnum() and a.startswith("B"):
            return a
        return None

    def _discover_tile_hint(self, node) -> str:
        for img in node.select("img[alt]"):
            alt = (img.get("alt") or "").strip()
            if len(alt) > 6 and not alt.lower().startswith("amazon"):
                return alt[:320]
        try:
            txt = node.get_text(" ", strip=True)
        except (AttributeError, TypeError):
            return ""
        return txt[:320] if txt else ""

    @staticmethod
    def _discover_should_skip_competitor_tile(primary_title: str, tile_hint: str) -> bool:
        """Skip accessory tiles when the primary is a handset — but only if the
        primary itself isn't an accessory.

        Without the accessory-primary check, an "iPhone 17 Pro Max case" primary
        triggered ``_HANDSET_PRIMARY_HINT`` (matches "iphone") and then dropped
        every other phone-case tile as "accessory noise". For an accessory primary,
        accessory peers ARE the comparison set; only the handset-vs-accessory mix
        is noise.
        """
        ph = (primary_title or "").strip()
        hint = (tile_hint or "").strip()
        if not _HANDSET_PRIMARY_HINT.search(ph):
            return False
        if _ACCESSORY_TILE_HINT.search(ph):
            return False
        return bool(_ACCESSORY_TILE_HINT.search(hint))

    @staticmethod
    def _leading_brand_token(title: str) -> str:
        t = (title or "").strip()
        m = re.match(r"^[\W]*([A-Za-z0-9][A-Za-z0-9+&.-]{0,31})", t)
        return (m.group(1).strip("+-._ ") if m else "").lower()

    @staticmethod
    def _hint_likely_same_brand(hint: str, brand: str) -> bool:
        """Heuristic: first visible product word in tile/search title matches leading brand."""
        if not brand or len(brand) < 2:
            return False
        words = re.findall(r"[A-Za-z0-9&]+", (hint or "").strip())
        if not words:
            return False
        first = words[0].casefold()
        br = brand.casefold()
        if first == br:
            return True
        # "boAt" vs "Boat" style folding (strip non-alnum)
        ff = re.sub(r"[^a-z0-9]+", "", first)
        bf = re.sub(r"[^a-z0-9]+", "", br)
        if ff and bf and (ff == bf or (len(bf) >= 3 and ff.startswith(bf[: min(4, len(bf))]))):
            return True
        return False

    def _serp_keywords_for_cross_shop(self, soup: BeautifulSoup, primary_title: str) -> str:
        """Build a storefront search query biased toward the category, not the house brand."""
        browse_l = (self._extract_browse_category(soup) or "").casefold()
        json_l = (self._extract_jsonld_product_category(soup) or "").casefold()
        blob = f"{browse_l} {json_l}"
        if any(x in blob for x in ("smart watch", "smartwatch", "wearable technology", "wrist watch")):
            return "bluetooth calling smart watch"
        if any(x in blob for x in ("headphone", "earbud", "ear bud", "in-ear", "over ear", "on-ear")):
            return "wireless earbuds with mic"
        if "laptop" in blob or "notebook" in blob:
            return "laptop computer"
        if any(x in blob for x in ("smartphone", "mobile phone", "basic mobiles")):
            return "android smartphone 5g"
        if any(x in blob for x in ("television", " smart tv", " led tv")):
            return "smart led tv"
        if "tablet" in blob:
            return "android tablet"
        if any(x in blob for x in ("refrigerator", "fridge", "freezer")):
            return "double door refrigerator"
        if any(x in blob for x in ("washing machine", "washer")):
            return "front load washing machine"
        if any(x in blob for x in ("air conditioner", " ac ", "split ac")):
            return "split inverter air conditioner"
        if any(x in blob for x in ("mixer", "grinder", "food processor")):
            return "mixer grinder 750w"
        if any(x in blob for x in ("trimmer", "shaver", "grooming")):
            return "beard trimmer for men"
        if any(x in blob for x in ("camera", "dslr", "mirrorless")):
            return "mirrorless camera"
        if any(x in blob for x in ("keyboard", "mouse", "monitor")):
            return "wireless keyboard mouse combo"
        return self._keywords_from_debranded_title(primary_title)

    _DEBRAND_TITLE_STOP = frozenset(
        {
            "with", "for", "and", "the", "men", "women", "unisex", "cm", "inch", "inches",
            "hd", "display", "storage", "ram", "gb", "tb", "mb", "mah", "w", "v", "hz",
            "black", "white", "silver", "blue", "red", "green", "grey", "gray", "gold",
            "active", "pro", "max", "plus", "mini", "new", "latest", "model",
        },
    )

    def _keywords_from_debranded_title(self, title: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9+]*", title or "")
        if tokens:
            tokens = tokens[1:]  # drop presumed brand / first token
        out: list[str] = []
        for t in tokens:
            tl = t.casefold()
            if tl in self._DEBRAND_TITLE_STOP or len(tl) < 2:
                continue
            out.append(t)
            if len(out) >= 6:
                break
        return " ".join(out) if out else "electronics"

    def _order_cross_brand_first(
        self, pairs: list[tuple[str, str]], brand: str,
    ) -> list[tuple[str, str]]:
        """Put tiles whose hint does not look like the same house brand first (cross-shop discovery)."""
        if not brand:
            return pairs
        cross: list[tuple[str, str]] = []
        same: list[tuple[str, str]] = []
        for a, h in pairs:
            if self._hint_likely_same_brand(h, brand):
                same.append((a, h))
            else:
                cross.append((a, h))
        return cross + same

    async def _discover_serp_asin_hints(
        self, keywords: str, amazon_domain: str, cap: int,
    ) -> list[tuple[str, str]]:
        """First search-results page ASINs + short title hints for diversification."""
        q = keywords.strip()
        if len(q) < 3:
            return []
        site = amazon_site_origin(amazon_domain)
        path_q = urlencode({"k": q})
        url = f"{site}/s?{path_q}"
        _canon, html, status = await self._fetch_html_lenient(url, amazon_domain)
        if status >= 400 or not html or self._looks_like_blocked(html):
            logger.info("SERP competitor discovery empty for k=%r status=%s", q[:96], status)
            return []
        soup = BeautifulSoup(html, "html.parser")
        rows: list[tuple[str, str]] = []
        seen_local: set[str] = set()
        selectors = (
            'div[data-component-type="s-search-result"]',
            "div.s-main-slot div.s-result-item[data-asin]",
        )
        for sel in selectors:
            for div in soup.select(sel):
                a = (div.get("data-asin") or "").strip().upper()
                if len(a) != 10 or not a.startswith("B") or a in seen_local:
                    continue
                seen_local.add(a)
                hint = ""
                for hsel in ("h2 a span.a-text-normal", "h2 span.a-text-normal", "h2 a", ".a-size-mini.a-spacing-none.a-color-base"):
                    el = div.select_one(hsel)
                    if el:
                        hint = el.get_text(" ", strip=True)
                        if hint and len(hint) > 4:
                            break
                rows.append((a, hint[:320]))
                if len(rows) >= cap:
                    return rows
            if len(rows) >= cap:
                break
        logger.info("SERP competitor discovery k=%r returned %d ASINs", q[:96], len(rows))
        return rows

    async def discover_competitor_asins(
        self,
        asin: str,
        amazon_domain: str,
        limit: int,
        *,
        candidate_pool_limit: int | None = None,
        spec: Any = None,
    ) -> list[str]:
        """Collect related ASINs from PDP widgets plus a category SERP pass for cross-brand peers.

        Amazon's similar-item carousels often skew to the same manufacturer. We merge those
        candidates with storefront search results derived from breadcrumbs / product type,
        then rank other-brand tiles ahead of same-brand so competitive sets include rivals.

        ``limit`` controls ranking depth; ``candidate_pool_limit`` (when set) expands how many
        ASINs are returned so callers can filter (category, price band, variants) and still keep
        nine distinct competitors.

        ``spec`` is an optional :class:`app.services.comparison_spec.ComparisonSpec` from
        Gemini. When present we (a) use ``spec.query`` for the storefront SERP pass and
        (b) filter widget tiles by ``spec.title_matches`` against the tile hint when the hint
        is non-empty (the per-PDP filter in the runner's pass 1 enforces it on canonical titles).
        """
        site = amazon_site_origin(amazon_domain)
        target = f"{site}/dp/{asin.upper()}"

        _canonical, html = await self._fetch_html(target, amazon_domain)
        if self._looks_like_blocked(html):
            return []

        soup = BeautifulSoup(html, "html.parser")
        primary = asin.upper()
        primary_title = self._extract_title(soup)
        pairs: list[tuple[str, str]] = []
        seen: set[str] = {primary}
        pool_requested = max(limit, candidate_pool_limit) if candidate_pool_limit is not None else limit
        gather_cap = max(pool_requested * 6, pool_requested + 12, 66)

        def _spec_allows(hint: str) -> bool:
            # Tile hints are noisy and short; only veto when the hint is long enough that a
            # mismatch is meaningful. Empty/short hints get filtered later via the canonical PDP title.
            if spec is None:
                return True
            h = (hint or "").strip()
            if len(h) < 10:
                return True
            return spec.title_matches(h)

        def try_add(cand: str, hint: str) -> None:
            if cand in seen or len(pairs) >= gather_cap:
                return
            if self._discover_should_skip_competitor_tile(primary_title, hint):
                return
            if not _spec_allows(hint):
                return
            seen.add(cand)
            pairs.append((cand, hint or ""))

        def harvest_selectors(selectors: tuple[str, ...]) -> None:
            for sel in selectors:
                for root in soup.select(sel):
                    for node in root.select("[data-asin]"):
                        cand = self._asin_from_data_asin_node(node)
                        if not cand:
                            continue
                        try_add(cand, self._discover_tile_hint(node))
                        if len(pairs) >= gather_cap:
                            return

        strict_selectors = (
            "#product-comparison_feature_div",
            '[cel_widget_id*="comparator"]',
            '[cel_widget_id*="similar"]',
            "#sims-consolidated-1_feature_div",
            "#sims-constraint-carousel_feature_div",
            "#sp_detail_thematic-asin_feature_div",
        )
        loose_selectors = ("[data-a-carousel-options]",)
        low_trust_selectors = ("#sims-fbt", "#sp_detail", "#sponsoredProducts_feature_div")

        harvest_selectors(strict_selectors)
        if len(pairs) < gather_cap:
            harvest_selectors(loose_selectors)
        if len(pairs) < gather_cap:
            harvest_selectors(low_trust_selectors)

        if len(pairs) < gather_cap:
            for node in soup.select("[data-asin]"):
                cand = self._asin_from_data_asin_node(node)
                if not cand:
                    continue
                try_add(cand, self._discover_tile_hint(node))
                if len(pairs) >= gather_cap:
                    break

        if len(pairs) < gather_cap:
            for a in soup.select('a[href*="/dp/"], a[href*="/gp/product/"], a[href*="/gp/aw/d/"]'):
                href = (a.get("href") or "") + ""
                m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", href, flags=re.I)
                if m:
                    cand = m.group(1).upper()
                    if cand.startswith("B") and len(cand) == 10:
                        try_add(cand, "")
                if len(pairs) >= gather_cap:
                    break

        # Prefer the Gemini-derived query when present (precise compatibility, e.g. "iPhone 17 case"
        # vs "iPhone 17 Pro case"); otherwise fall back to the breadcrumb/title heuristic.
        kw = (spec.query if spec is not None else "").strip() or self._serp_keywords_for_cross_shop(
            soup, primary_title,
        )
        if kw and len(pairs) < gather_cap:
            serp_cap = min(max(28, pool_requested + 8), gather_cap)
            for a, h in await self._discover_serp_asin_hints(kw, amazon_domain, cap=serp_cap):
                try_add(a, h)

        brand = self._leading_brand_token(primary_title)
        ordered = self._order_cross_brand_first(pairs, brand)
        return [a for a, _ in ordered[:pool_requested]]

    async def fetch_best_seller_asins(self, bestsellers_page_url: str, amazon_domain: str, limit: int) -> list[str]:
        domain_hint = normalize_amazon_domain(amazon_domain)
        target = bestsellers_page_url.strip()
        if not target.startswith("http"):
            target = f"{amazon_site_origin(domain_hint)}{'/' + target if not target.startswith('/') else target}"

        _canonical, html = await self._fetch_html(target, domain_hint)
        soup = BeautifulSoup(html, "html.parser")

        asins: list[str] = []
        seen: set[str] = set()

        for node in soup.select("[data-asin]"):
            a = node.get("data-asin") or ""
            a = str(a).strip().upper()
            if len(a) == 10 and a.startswith("B") and a not in seen:
                seen.add(a)
                asins.append(a)

        if len(asins) < limit:
            for a in soup.select('a[href*="/dp/"], a[href*="/gp/product/"], a[href*="/gp/aw/d/"]'):
                href = (a.get("href") or "") + ""
                m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", href, flags=re.I)
                if m:
                    val = m.group(1).upper()
                    if val not in seen and val.startswith("B"):
                        seen.add(val)
                        asins.append(val)
                if len(asins) >= limit:
                    break

        if not asins and self.save_html_on_empty:
            slug = hashlib.sha256(target.encode()).hexdigest()[:16]
            self._dump_debug_html("bestsellers", slug, html, "zero_asins")

        return asins[:limit]

    @staticmethod
    def _review_block_has_customer_images(blk) -> bool:
        """True when the review DOM includes customer-uploaded photo tiles (Amazon markup varies)."""
        selectors = (
            '[data-hook="review-image-tile"]',
            '[data-hook="setup-image-modal"]',
            "div.review-image-tile-section",
            "ul[data-hook='image-block-tiles']",
            ".review-image-tile-container img",
            'img[data-hook="review-image-tile"]',
            '[data-hook="image-block-tiles"]',
        )
        for sel in selectors:
            if blk.select_one(sel):
                return True
        for img in blk.select("img"):
            alt = (img.get("alt") or "") + " " + (img.get("title") or "")
            if re.search(r"customer\s+image|customer\s+photo|from\s+the\s+manufacturer", alt, re.I):
                return True
        return False

    def _reviews_from_page(self, asin: str, soup: BeautifulSoup, page: int) -> list[NormalizedReview]:
        out: list[NormalizedReview] = []
        blocks = soup.select(
            '[id^="customer_review-"], div[data-hook="review"], li[data-hook="review"], div.review[data-hook="review"]'
        )

        if not blocks:
            blocks = soup.select("div.review")

        for idx, blk in enumerate(blocks):
            star = blk.select_one(
                'i[data-hook="review-star-rating"], '
                'i[data-hook="cmps-review-star-rating"], '
                ".review-rating span, i.review-rating span, span[data-hook='review-star-rating']"
            )
            rating: Optional[int] = None
            if star:
                aria = (star.get("aria-label") or "") + star.get_text(" ", strip=True)
                dm = re.search(r"([\d,.]+)\s*(?:out\s+of|/|von|sur|sur\s+5)", aria, flags=re.I)
                if not dm:
                    dm = re.search(r"([\d,.]+)\s*/\s*5", aria)
                if dm:
                    try:
                        val = float(dm.group(1).replace(",", "."))
                        rating = max(1, min(5, int(round(val))))
                    except (TypeError, ValueError):
                        rating = None

            parts: list[str] = []
            for sp in blk.select('[data-hook="review-title"] span, a[data-hook="review-title"] span'):
                t = sp.get_text(strip=True)
                if not t:
                    continue
                if re.match(r"^\s*[\d.]+\s*out\s+of\s*5\s*stars\s*$", t, flags=re.I):
                    continue
                if re.match(r"^\s*[\d.]+\s*/\s*5\s*$", t):
                    continue
                parts.append(t)
            rtitle = " ".join(parts).strip() if parts else None
            if not rtitle:
                title_el = blk.select_one(
                    '[data-hook="review-title"] span:last-of-type, '
                    'a[data-hook="review-title"] span, '
                    '[data-hook="review-title"]'
                )
                rtitle = title_el.get_text(strip=True) if title_el else None
            if rtitle:
                rtitle = _REVIEW_TITLE_STAR_PREFIX.sub("", rtitle).strip() or None
            if rtitle and len(rtitle) > 512:
                rtitle = rtitle[:512]

            body_el = blk.select_one(
                '[data-hook="review-body"] span, '
                '[data-hook="review-body"], '
                "span.review-text span, div.reviewText"
            )
            body = body_el.get_text("\n", strip=True) if body_el else ""

            date_el = blk.select_one('[data-hook="review-date"], span.review-date, span.review-date-title')
            rdate = date_el.get_text(strip=True) if date_el else None

            rid_el = blk.get("id") or ""
            stable = blk.get("data-hook-collapsed-video-id") or ""
            slug = stable or rid_el or ""
            digest = hashlib.sha256(f"{asin}|{page}|{idx}|{rtitle}|{body[:240]}".encode()).hexdigest()[:48]
            ext_id = f"{asin}-{digest}" if len(slug) < 8 else slug

            vp = False
            blob = blk.get_text(" ", strip=True)
            if blob:
                vp = bool(
                    re.search(
                        r"Verified Purchase|Achat vérifié|Verifizierter Kauf|Compra verificada",
                        blob,
                        re.I,
                    )
                )

            if body.strip() or rating is not None or rtitle:
                has_img = self._review_block_has_customer_images(blk)
                out.append(
                    NormalizedReview(
                        external_id=ext_id,
                        rating=rating,
                        title=rtitle,
                        body=body[:8192],
                        review_date=rdate[:32] if rdate else None,
                        is_verified_purchase=vp,
                        has_customer_images=has_img,
                    )
                )

        return out

    async def fetch_reviews_page(
        self,
        asin: str,
        amazon_domain: str,
        page_token: Optional[str],
    ) -> tuple[list[NormalizedReview], Optional[str]]:
        """Try several strategies in order; return first non-empty page.

        1. Canonical product-reviews paging URL (preferred when Amazon serves it).
        2. ScraperAPI structured Amazon reviews endpoint (JSON, more reliable on .in).
        3. Fallback to the PDP and parse the embedded top-reviews block.

        Repeated 4xx responses are swallowed (job continues, just with fewer reviews).
        """
        site = amazon_site_origin(amazon_domain)
        page = int(page_token) if page_token and str(page_token).isdigit() else 1
        upper_asin = asin.upper()

        parts = urlsplit(site)
        query = urlencode(
            {"ie": "UTF8", "reviewerType": "all_reviews", "sortBy": "recent", "pageNumber": str(page)},
        )
        # Amazon's canonical paginated URL — the "/ref=cm_cr_arp_d_paging_btm_next_<N>"
        # variant succeeds more often on .in than the bare "?pageNumber" form.
        path = f"/product-reviews/{upper_asin}/ref=cm_cr_arp_d_paging_btm_next_{page}"
        target = urlunsplit((parts.scheme, parts.netloc, path, query, ""))
        alt_path = f"/product-reviews/{upper_asin}"
        alt_target = urlunsplit((parts.scheme, parts.netloc, alt_path, query, ""))

        canonical, html, status = await self._fetch_html_lenient(target, amazon_domain)

        if status >= 400:
            logger.info(
                "Reviews page %s for %s returned HTTP %s; trying structured fallback.",
                page,
                upper_asin,
                status,
            )
            html = ""

        reviews: list[NormalizedReview] = []
        next_token: Optional[str] = None
        strategy = ""

        if html:
            soup = BeautifulSoup(html, "html.parser")
            reviews = self._reviews_from_page(upper_asin, soup, page)
            if reviews:
                strategy = "product_reviews_html"
                next_anchor = soup.select_one('li.a-last:not(.a-disabled) a')
                next_token = str(page + 1) if next_anchor else None
                if next_token is None and len(reviews) >= 8:
                    next_token = str(page + 1)
            elif self.save_html_on_empty:
                self._dump_debug_html("reviews", f"{upper_asin}_p{page}", html, "zero_reviews")

        # If the first non-rendered pass yielded zero reviews (empty body or a JS-only shell),
        # retry once with render=true. This used to be amazon.in-only; on recent runs we've
        # seen the same starvation on .com / .co.uk too (especially after ScraperAPI 4xx
        # short-circuits the body to ""), so the render fallback applies to all storefronts now.
        if not reviews and not self.render:
            tag_prefix = "in_prerender" if self._amazon_in_client_render_heavy(amazon_domain) else "prerender"
            for attempt_url, tag in (
                (target, f"{tag_prefix}_ref"),
                (alt_target, f"{tag_prefix}_alt"),
            ):
                if reviews:
                    break
                if tag.endswith("_alt") and alt_target == target:
                    continue
                rc, hr, sr = await self._fetch_html_lenient(attempt_url, amazon_domain, force_render=True)
                if sr < 400 and hr:
                    soup_r = BeautifulSoup(hr, "html.parser")
                    rr = self._reviews_from_page(upper_asin, soup_r, page)
                    if rr:
                        reviews = rr
                        canonical = rc
                        strategy = f"product_reviews_html_{tag}"
                        na_r = soup_r.select_one('li.a-last:not(.a-disabled) a')
                        next_token = str(page + 1) if na_r else None
                        if next_token is None and len(rr) >= 8:
                            next_token = str(page + 1)
                        break

        if not reviews:
            struct_reviews, struct_next, struct_status = await self._fetch_structured_reviews(
                upper_asin, amazon_domain, page,
            )
            if struct_reviews:
                reviews = struct_reviews
                next_token = struct_next
                strategy = "structured_endpoint"
            else:
                logger.info(
                    "Structured reviews fallback empty for %s p%s (status=%s).",
                    upper_asin,
                    page,
                    struct_status,
                )

        if not reviews:
            # Structured endpoint with render fallback — used to be amazon.in only; some .com PDPs
            # also need it when the structured JSON returns empty without a rendered upstream.
            struct2, struct_next2, st2 = await self._fetch_structured_reviews(
                upper_asin, amazon_domain, page, force_render=True,
            )
            if struct2:
                reviews = struct2
                next_token = struct_next2
                strategy = "structured_endpoint_render"

        if not reviews and page == 1:
            pdp_target = f"{site}/dp/{upper_asin}"
            pdp_canonical, pdp_html, pdp_status = await self._fetch_html_lenient(pdp_target, amazon_domain)
            if pdp_status < 400 and pdp_html:
                soup = BeautifulSoup(pdp_html, "html.parser")
                pdp_reviews = self._reviews_from_page(upper_asin, soup, page)
                if pdp_reviews:
                    reviews = pdp_reviews
                    next_token = None  # PDP only embeds a small sample.
                    strategy = "pdp_embedded"
                    canonical = pdp_canonical
                elif self.save_html_on_empty:
                    self._dump_debug_html("pdp_reviews", upper_asin, pdp_html, "pdp_zero_reviews")

        if not reviews and page == 1 and not self.render:
            # Final fallback: render the PDP and parse the embedded top-reviews block.
            # Generalised from amazon.in-only because empty-body 4xx responses elsewhere
            # also leave the PDP fallback without anything to scrape.
            pdp_target = f"{site}/dp/{upper_asin}"
            pc, ph, ps = await self._fetch_html_lenient(pdp_target, amazon_domain, force_render=True)
            if ps < 400 and ph:
                soup_p = BeautifulSoup(ph, "html.parser")
                pr = self._reviews_from_page(upper_asin, soup_p, page)
                if pr:
                    reviews = pr
                    next_token = None
                    strategy = "pdp_embedded_render"
                    canonical = pc

        if not reviews and page == 1 and alt_target != target:
            ac2, h2, st2 = await self._fetch_html_lenient(alt_target, amazon_domain)
            if st2 < 400 and h2:
                soup2 = BeautifulSoup(h2, "html.parser")
                r2 = self._reviews_from_page(upper_asin, soup2, page)
                if r2:
                    reviews = r2
                    canonical = ac2
                    strategy = "product_reviews_html_alt"
                    next_anchor2 = soup2.select_one('li.a-last:not(.a-disabled) a')
                    next_token = str(page + 1) if next_anchor2 else None
                    if next_token is None and len(r2) >= 8:
                        next_token = str(page + 1)
                elif self.save_html_on_empty:
                    self._dump_debug_html("reviews_alt", f"{upper_asin}_p{page}", h2, "zero_reviews")

        if reviews and strategy:
            logger.info(
                "Fetched %d reviews for %s p%s via %s.", len(reviews), upper_asin, page, strategy,
            )

        return reviews, next_token
