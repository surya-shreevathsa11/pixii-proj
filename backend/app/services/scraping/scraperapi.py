"""
Amazon HTML ingestion via https://www.scraperapi.com/ proxy.

Markup varies by locale (amazon.com vs amazon.co.uk, etc.) and changes over time.
Use SCRAPERAPI_RENDER=true when the DOM is mostly client-rendered, set AMAZON_DOMAIN
to match the storefront, and enable SCRAPERAPI_SAVE_HTML_ON_EMPTY to capture HTML
samples for tightening selectors locally.
"""

from __future__ import annotations

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
    r"applecare|care\s*\+|protection\s+plans?|extended\s+warrant|warranty\s+service|"
    r"\bcases?\b|\bcovers?\b|flip\s+cover|bumper|skins?|tempered\s+glass|screen\s+guards?|protectors?\b|"
    r"magnetic\s+wallet|silicone\s+case|earbuds?|airpods)\b",
    re.I,
)
_HANDSET_PRIMARY_HINT = re.compile(
    r"\b(iphone|pixel\s+\d|galaxy\s+s\d|galaxy\s+z\s|galaxy\s+a\d|oneplus|xiaomi|redmi|poco|nothing\s+phone|"
    r"oppo\s+reno|realme|vivo|motorola|smartphone|mobile\s+phones?|5g\s+phone)\b",
    re.I,
)
_REVIEW_TITLE_STAR_PREFIX = re.compile(r"^\s*[\d.]+\s*out\s+of\s*5\s*stars\s*", re.I)


class ScraperApiScrapingProvider:
    """https://docs.scraperapi.com/making-requests/http-get-requests-get"""

    BASE = "https://api.scraperapi.com"

    def __init__(
        self,
        api_key: str,
        timeout: float = 120.0,
        render: bool = False,
        country_code: Optional[str] = None,
        save_html_on_empty: bool = False,
    ):
        self.api_key = api_key
        self.timeout = timeout
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
        # ScraperAPI: every API flag must appear *before* `url` in the query string, or routing can break (often 404).
        # See https://docs.scraperapi.com/synchronous-apis/using-the-api-endpoint
        query_pairs: list[tuple[str, str]] = [("api_key", self.api_key)]
        if self.render or force_render:
            query_pairs.append(("render", "true"))
        country = self._country_for(amazon_domain)
        if country:
            query_pairs.append(("country_code", country))
        query_pairs.append(("url", canonical))

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            rsp = await client.get(self.BASE, params=query_pairs)

        if raise_on_error:
            rsp.raise_for_status()

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

    async def _fetch_structured_reviews(
        self,
        asin: str,
        amazon_domain: str,
        page: int,
    ) -> tuple[list[NormalizedReview], Optional[str], int]:
        """Use ScraperAPI's structured Amazon Reviews endpoint as a fallback.

        Docs: https://docs.scraperapi.com/structured-data-collection-method/amazon/amazon-review
        Returns (reviews, next_token, status_code). Status 0 means the call itself failed.
        """
        domain = normalize_amazon_domain(amazon_domain)
        # tld is the part *after* "amazon." (e.g. "in", "co.uk", "com").
        tld = domain[len("amazon."):] if domain.startswith("amazon.") else "com"
        country = self._country_for(amazon_domain) or "us"

        params: list[tuple[str, str]] = [
            ("api_key", self.api_key),
            ("asin", asin.upper()),
            ("tld", tld),
            ("country", country),
            ("page", str(page)),
        ]
        if self.render:
            params.append(("render", "true"))

        url = "https://api.scraperapi.com/structured/amazon/review"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                rsp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning("ScraperAPI structured reviews call failed for %s p%s: %s", asin, page, exc)
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

    async def fetch_listing(self, asin: str, amazon_domain: str) -> NormalizedListing:
        site = amazon_site_origin(amazon_domain)
        target = f"{site}/dp/{asin.upper()}"

        canonical, html = await self._fetch_html(target, amazon_domain)

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
                raw={"provider": "scraperapi", "error": "block_or_challenge", "html_sample": html[:500]},
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
        ph = (primary_title or "").strip()
        hint = (tile_hint or "").strip()
        if not _HANDSET_PRIMARY_HINT.search(ph):
            return False
        return bool(_ACCESSORY_TILE_HINT.search(hint))

    async def discover_competitor_asins(self, asin: str, amazon_domain: str, limit: int) -> list[str]:
        """Collect related ASINs from PDP widgets.

        Prefer compare/similar carousels; de-prioritize FBT and sponsored blocks where chargers
        and AppleCare often appear. When the PDP looks like a handset, drop accessory-like tiles.
        """
        site = amazon_site_origin(amazon_domain)
        target = f"{site}/dp/{asin.upper()}"

        _canonical, html = await self._fetch_html(target, amazon_domain)
        if self._looks_like_blocked(html):
            return []

        soup = BeautifulSoup(html, "html.parser")
        primary = asin.upper()
        primary_title = self._extract_title(soup)
        candidates: list[str] = []
        seen: set[str] = {primary}
        gather_cap = max(limit * 4, limit + 8)

        def try_add(cand: str, hint: str) -> None:
            if cand in seen or len(candidates) >= gather_cap:
                return
            if self._discover_should_skip_competitor_tile(primary_title, hint):
                return
            seen.add(cand)
            candidates.append(cand)

        def harvest_selectors(selectors: tuple[str, ...]) -> None:
            for sel in selectors:
                for root in soup.select(sel):
                    for node in root.select("[data-asin]"):
                        cand = self._asin_from_data_asin_node(node)
                        if not cand:
                            continue
                        try_add(cand, self._discover_tile_hint(node))
                        if len(candidates) >= gather_cap:
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
        if len(candidates) < limit:
            harvest_selectors(loose_selectors)
        if len(candidates) < limit:
            harvest_selectors(low_trust_selectors)

        if len(candidates) < limit:
            for node in soup.select("[data-asin]"):
                cand = self._asin_from_data_asin_node(node)
                if not cand:
                    continue
                try_add(cand, self._discover_tile_hint(node))
                if len(candidates) >= gather_cap:
                    break

        if len(candidates) < limit:
            for a in soup.select('a[href*="/dp/"], a[href*="/gp/product/"], a[href*="/gp/aw/d/"]'):
                href = (a.get("href") or "") + ""
                m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", href, flags=re.I)
                if m:
                    cand = m.group(1).upper()
                    if cand.startswith("B") and len(cand) == 10:
                        try_add(cand, "")
                if len(candidates) >= gather_cap:
                    break

        return candidates[:limit]

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

        store_host = normalize_amazon_domain(amazon_domain).lower()
        if not reviews and page == 1 and not self.render and store_host.endswith("amazon.in"):
            for attempt_url, tag in ((target, "render_ref"), (alt_target, "render_alt")):
                if reviews:
                    break
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

        if reviews and strategy:
            logger.info(
                "Fetched %d reviews for %s p%s via %s.", len(reviews), upper_asin, page, strategy,
            )

        return reviews, next_token
