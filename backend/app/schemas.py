import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import JobFlow, JobStatus


class CompetitiveJobCreate(BaseModel):
    product_url: str = Field(..., description="Amazon product URL or ASIN for your listing")
    competitor_urls: list[str] = Field(default_factory=list)
    auto_discover_competitors: bool = Field(
        default=True,
        description="When true, competitor ASINs are scraped from the primary PDP (similar/compare widgets); leave competitor_urls empty.",
    )

    @field_validator("competitor_urls")
    @classmethod
    def sanitize_competitors(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) > 9:
            raise ValueError("Provide at most nine competitor URLs.")
        return cleaned

    @model_validator(mode="after")
    def auto_vs_manual_competitors(self):
        if self.auto_discover_competitors and self.competitor_urls:
            raise ValueError("Remove competitor_urls when auto_discover_competitors is true, or turn off auto-discover.")
        return self


class MarketJobCreate(BaseModel):
    bestsellers_url: str = Field(..., description="Amazon Best Sellers category page URL")


class JobCreateResponse(BaseModel):
    job_id: uuid.UUID


class ListingOut(BaseModel):
    asin: str
    title: str
    price: Optional[float]
    currency: str
    bsr_rank: Optional[int]
    bsr_category: Optional[str]
    product_category: Optional[str] = None
    avg_rating: Optional[float]
    review_count: Optional[int]
    canonical_url: Optional[str]
    estimated_monthly_units: Optional[float]
    estimated_monthly_revenue: Optional[float]  # Always INR
    previous_month_units: Optional[int] = None
    revenue_basis: str = "unknown"
    unit_price_inr: Optional[float] = None


class SummaryOut(BaseModel):
    asin: str
    product_title: str = ""
    final_summary: str
    key_purchase_criteria: list[str]
    why_buyers_like: Optional[str] = None
    why_buyers_caution: Optional[str] = None


class ReviewOut(BaseModel):
    asin: str
    rating: Optional[int] = None
    title: Optional[str] = None
    body: str = ""
    review_date: Optional[str] = None
    has_customer_images: bool = False
    verified: bool = False


class JobDetailResponse(BaseModel):
    id: uuid.UUID
    flow: JobFlow
    status: JobStatus
    phase: str
    error_message: Optional[str]
    bestsellers_url: Optional[str]
    product_url: Optional[str]
    competitor_urls: list[str]
    asins: list[str]
    market_totals_note: Optional[str]
    listings: list[ListingOut]
    summaries: list[SummaryOut]
    reviews: list[ReviewOut] = Field(default_factory=list)
    reviews_count_total: int
    created_at: datetime
    ingest_demo: bool = False
    gemini_configured: bool = False


class BootstrapResponse(BaseModel):
    scraping_provider: str
    gemini_configured: bool
