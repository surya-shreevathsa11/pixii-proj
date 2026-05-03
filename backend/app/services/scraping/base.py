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
    # Number of units sold last month, taken from the PDP "X bought in past month" badge
    # (lower bound when the badge says "X+"). None when Amazon doesn't surface it.
    previous_month_units: Optional[int] = None
    # Raw badge text we parsed, useful for showing the source in the UI.
    previous_month_label: Optional[str] = None
    # Browse path / JSON-LD when BSR category line is missing.
    product_category: Optional[str] = None


@dataclass
class NormalizedReview:
    external_id: str
    rating: Optional[int]
    title: Optional[str]
    body: str
    review_date: Optional[str]
    is_verified_purchase: bool
    has_customer_images: bool = False


class ScrapingProvider(Protocol):
    async def fetch_listing(self, asin: str, amazon_domain: str) -> NormalizedListing: ...

    async def fetch_best_seller_asins(self, bestsellers_page_url: str, amazon_domain: str, limit: int) -> list[str]: ...

    async def fetch_reviews_page(
        self,
        asin: str,
        amazon_domain: str,
        page_token: Optional[str],
    ) -> tuple[list[NormalizedReview], Optional[str]]: ...

    async def discover_competitor_asins(self, asin: str, amazon_domain: str, limit: int) -> list[str]: ...
