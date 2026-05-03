"""
Amazon HTML ingestion via https://www.scraperapi.com/ proxy.

Markup varies by locale (amazon.com vs amazon.co.uk, etc.) and changes over time.
Use SCRAPERAPI_RENDER=true when the DOM is mostly client-rendered, set AMAZON_DOMAIN
to match the storefront, and enable SCRAPERAPI_SAVE_HTML_ON_EMPTY to capture HTML
samples for tightening selectors locally.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
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
        canonical = self._canonical_target_url(target_url)
        # ScraperAPI: every API flag must appear *before* `url` in the query string, or routing can break (often 404).
        # See https://docs.scraperapi.com/synchronous-apis/using-the-api-endpoint
        query_pairs: list[tuple[str, str]] = [("api_key", self.api_key)]
        if self.render:
            query_pairs.append(("render", "true"))
        country = self._country_for(amazon_domain)
        if country:
            query_pairs.append(("country_code", country))
        query_pairs.append(("url", canonical))

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            rsp = await client.get(self.BASE, params=query_pairs)

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

        return canonical, html

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
        if "₹" in t or "rs." in t.lower() or "inr" in t.lower():
            return "INR"
        if "$" in t or "usd" in t.lower():
            return "USD"
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
                s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None

    def _extract_price_currency(self, soup: BeautifulSoup) -> tuple[Optional[float], str]:
        whole_sels = soup.select(".a-price:not(.a-text-price) .a-price-whole")
        frac_sels = soup.select(".a-price:not(.a-text-price) .a-price-fraction")
        if whole_sels:
            integral = "".join(ch for ch in whole_sels[0].get_text() if ch.isdigit())
            fr = frac_sels[0].get_text(strip=True) if frac_sels else ""
            fr_digits = ("".join(ch for ch in fr if ch.isdigit()) + "00")[:2]
            if integral:
                try:
                    amt = float(f"{integral}.{fr_digits}")
                    parent_txt = ""
                    p = whole_sels[0].find_parent(class_=re.compile("a-price"))
                    if p:
                        parent_txt = p.get_text(" ", strip=True)
                    cur = self._currency_from_price_text(parent_txt or whole_sels[0].get_text(" ", strip=True))
                    return amt, cur
                except ValueError:
                    pass

        for sel in (
            'span[data-a-color="price"] span.a-offscreen',
            "span.a-price.a-text-price span.a-offscreen",
            "span.a-offscreen",
            "#corePrice_feature_div span.a-offscreen",
            "#apex_desktop span.a-offscreen",
            "#twister-plus-price-data-price span.a-offscreen",
        ):
            hidden = soup.select_one(sel)
            if not hidden:
                continue
            txt = hidden.get_text(strip=True)
            if not txt or len(txt) > 64:
                continue
            m_cur = re.search(r"([\d][\d.,]*)", txt)
            if not m_cur:
                continue
            amt = self._parse_amount_string(m_cur.group(1))
            if amt is None:
                continue
            cur = self._currency_from_price_text(txt)
            return amt, cur

        return None, "USD"

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
            r"Classement\s+des\s+meilleures\s+ventes[^\n#]*?n[°o]\s*([\d\s]+)\s+en\s+([^(\n<#]{3,160})",
            r"#([\d,.]+(?:,\d{3})*)\s+in\s+([A-Za-zÀ-ÿ0-9 &,'\-]{3,140})",
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

    def _listing_from_soup(self, asin: str, soup: BeautifulSoup, canonical: str, raw_html_len: int) -> NormalizedListing:
        html_blob = soup.decode()

        title_raw = self._extract_title(soup)
        title = title_raw or f"Amazon product {asin}"

        avg = self._extract_avg_rating(soup)
        review_count = self._extract_review_count(soup)

        price, currency = self._extract_price_currency(soup)
        bsr_rank, bsr_cat = self._extract_bsr(soup, html_blob[:200_000])

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
            raw={
                "provider": "scraperapi",
                "html_chars": raw_html_len,
                "canonical_url": canonical,
                "parse_thin": thin_parse,
                "render": self.render,
                "country_code": self.country_code,
                "warnings": warnings,
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
                raw={"provider": "scraperapi", "error": "block_or_challenge", "html_sample": html[:500]},
            )

        soup = BeautifulSoup(html, "html.parser")
        listing = self._listing_from_soup(asin.upper(), soup, canonical, len(html))

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

    async def discover_competitor_asins(self, asin: str, amazon_domain: str, limit: int) -> list[str]:
        """Collect related ASINs from PDP widgets (similar / compare / sponsored carousels)."""
        site = amazon_site_origin(amazon_domain)
        target = f"{site}/dp/{asin.upper()}"

        _canonical, html = await self._fetch_html(target, amazon_domain)
        if self._looks_like_blocked(html):
            return []

        soup = BeautifulSoup(html, "html.parser")
        primary = asin.upper()
        out: list[str] = []
        seen: set[str] = {primary}

        priority_selectors = (
            "[data-a-carousel-options]",
            "#product-comparison_feature_div",
            "#sims-fbt",
            "#sp_detail",
            "#sponsoredProducts_feature_div",
            '[cel_widget_id*="comparator"]',
            '[cel_widget_id*="similar"]',
            "#sims-consolidated-1_feature_div",
            "#sims-constraint-carousel_feature_div",
            "#sp_detail_thematic-asin_feature_div",
        )

        def harvest(root) -> None:
            for node in root.select("[data-asin]"):
                cand = self._asin_from_data_asin_node(node)
                if cand and cand not in seen:
                    seen.add(cand)
                    out.append(cand)
                    if len(out) >= limit:
                        return

        for sel in priority_selectors:
            for root in soup.select(sel):
                harvest(root)
                if len(out) >= limit:
                    return out[:limit]

        harvest(soup)
        if len(out) >= limit:
            return out[:limit]

        for a in soup.select('a[href*="/dp/"], a[href*="/gp/product/"], a[href*="/gp/aw/d/"]'):
            href = (a.get("href") or "") + ""
            m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", href, flags=re.I)
            if m:
                cand = m.group(1).upper()
                if cand not in seen and cand.startswith("B") and len(cand) == 10:
                    seen.add(cand)
                    out.append(cand)
            if len(out) >= limit:
                break

        return out[:limit]

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

            title_el = blk.select_one(
                '[data-hook="review-title"] span:last-of-type, '
                'a[data-hook="review-title"] span, '
                '[data-hook="review-title"]'
            )
            rtitle = title_el.get_text(strip=True) if title_el else None

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
        site = amazon_site_origin(amazon_domain)

        page = int(page_token) if page_token and str(page_token).isdigit() else 1

        path = f"/product-reviews/{asin.upper()}"
        query = urlencode(
            {"ie": "UTF8", "reviewerType": "all_reviews", "sortBy": "recent", "pageNumber": str(page)},
        )

        parts = urlsplit(site)
        target = urlunsplit((parts.scheme, parts.netloc, path, query, ""))

        _canonical, html = await self._fetch_html(target, amazon_domain)
        soup = BeautifulSoup(html, "html.parser")

        reviews = self._reviews_from_page(asin.upper(), soup, page)

        if not reviews and self.save_html_on_empty:
            self._dump_debug_html("reviews", f"{asin.upper()}_p{page}", html, "zero_reviews")

        next_anchor = soup.select_one('li.a-last:not(.a-disabled) a')
        next_token = str(page + 1) if (next_anchor and reviews) else None

        if reviews and next_token is None and len(reviews) >= 8:
            next_token = str(page + 1)

        return reviews, next_token
