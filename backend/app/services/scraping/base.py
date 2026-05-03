from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass
class NormalizedListing:
    asin: str
    title: str
    price: Optional[float]
    currency: str
    bsr_rank: Optional[int]
    bsr_category: Optional[str]
    avg_rating: Optional[float]
    review_count: Optional[int]
    canonical_url: Optional[str]
    raw: dict[str, Any]


@dataclass
class NormalizedReview:
    external_id: str
    rating: Optional[int]
    title: Optional[str]
    body: str
    review_date: Optional[str]
    is_verified_purchase: bool


class ScrapingProvider(Protocol):
    async def fetch_listing(self, asin: str, amazon_domain: str) -> NormalizedListing: ...

    async def fetch_best_seller_asins(self, bestsellers_page_url: str, amazon_domain: str, limit: int) -> list[str]: ...

    async def fetch_reviews_page(
        self,
        asin: str,
        amazon_domain: str,
        page_token: Optional[str],
    ) -> tuple[list[NormalizedReview], Optional[str]]: ...
