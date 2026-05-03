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
    avg_rating: Optional[float]
    review_count: Optional[int]
    canonical_url: Optional[str]
    estimated_monthly_units: Optional[float]
    estimated_monthly_revenue: Optional[float]


class SummaryOut(BaseModel):
    asin: str
    product_title: str = ""
    final_summary: str
    key_purchase_criteria: list[str]


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
    reviews_count_total: int
    created_at: datetime
    ingest_demo: bool = False
    gemini_configured: bool = False


class BootstrapResponse(BaseModel):
    scraping_provider: str
    gemini_configured: bool
