import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models import JobFlow, JobStatus


class CompetitiveJobCreate(BaseModel):
    product_url: str = Field(..., description="Amazon product URL for your listing")
    competitor_urls: list[str] = Field(default_factory=list)

    @field_validator("competitor_urls")
    @classmethod
    def sanitize_competitors(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) > 9:
            raise ValueError("Provide at most nine competitor URLs.")
        return cleaned


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
