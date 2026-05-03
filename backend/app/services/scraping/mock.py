import hashlib
from typing import Any, Optional

from app.services.scraping.base import NormalizedListing, NormalizedReview


def _pseudo(asin: str, salt: bytes) -> int:
    return int(hashlib.sha256((asin.encode() + salt)).hexdigest()[:8], 16)


class MockScrapingProvider:
    async def fetch_listing(self, asin: str, amazon_domain: str) -> NormalizedListing:
        r = max(50, (_pseudo(asin, b"bsr") % 4900) + 100)
        return NormalizedListing(
            asin=asin.upper(),
            title=f"Demo product ({asin.upper()})",
            price=float(9.99 + (_pseudo(asin, b"p") % 9000) / 100),
            currency="USD",
            bsr_rank=r,
            bsr_category="Health & Household",
            avg_rating=3.9 + (_pseudo(asin, b"star") % 110) / 100,
            review_count=int(120 + (_pseudo(asin, b"rv") % 9000)),
            canonical_url=f"https://www.{amazon_domain}/dp/{asin.upper()}",
            raw={"demo": True, "asin": asin.upper()},
        )

    async def fetch_best_seller_asins(self, bestsellers_page_url: str, amazon_domain: str, limit: int) -> list[str]:
        base = bestsellers_page_url.encode()
        out: list[str] = []
        seen: set[str] = set()
        i = 0
        alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"

        while len(out) < limit:
            digest = hashlib.sha256(base + str(i).encode()).hexdigest()
            suffix = "".join(alphabet[int(digest[j : j + 2], 16) % len(alphabet)] for j in range(0, 16, 2))[:9]
            a = ("B0" + suffix)[:10]
            if a not in seen:
                seen.add(a)
                out.append(a)
            i += 1

        return out

    async def discover_competitor_asins(self, asin: str, amazon_domain: str, limit: int) -> list[str]:
        base = asin.upper().encode() + b"|rivals|" + amazon_domain.encode()
        out: list[str] = []
        seen: set[str] = {asin.upper()}
        i = 0
        alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        while len(out) < limit:
            digest = hashlib.sha256(base + str(i).encode()).hexdigest()
            suffix = "".join(alphabet[int(digest[j : j + 2], 16) % len(alphabet)] for j in range(0, 16, 2))[:9]
            a = ("B0" + suffix)[:10]
            if a not in seen:
                seen.add(a)
                out.append(a)
            i += 1
        return out

    async def fetch_reviews_page(
        self,
        asin: str,
        amazon_domain: str,
        page_token: Optional[str],
    ) -> tuple[list[NormalizedReview], Optional[str]]:
        page = int(page_token) if page_token and page_token.isdigit() else 1
        if page > 40:
            return [], None

        rows: list[NormalizedReview] = []
        seed = _pseudo(asin, b"page") + page * 997
        for i in range(10):
            body = (
                f"Review {seed + i}: taste was ok, wished for clearer dosage. "
                f"Buying again if price stays low. Vegan label matters to me ({asin})."
            )
            has_img = (seed + i) % 4 == 0
            rows.append(
                NormalizedReview(
                    external_id=f"{asin}-{page}-{i}",
                    rating=3 + (seed + i) % 3,
                    title="Honest demo review",
                    body=body,
                    review_date=f"2024-{1 + ((seed + i) % 12):02}-15",
                    is_verified_purchase=True,
                    has_customer_images=has_img,
                )
            )
        return rows, str(page + 1)
