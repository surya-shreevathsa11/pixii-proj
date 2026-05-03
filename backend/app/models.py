import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobFlow(str, Enum):
    market = "market"
    competitive = "competitive"


class Job(SQLModel, table=True):
    __tablename__ = "job"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    flow: JobFlow = Field(index=True)
    status: JobStatus = Field(default=JobStatus.queued, index=True)
    phase: str = Field(default="")
    error_message: Optional[str] = Field(default=None)
    bestsellers_url: Optional[str] = Field(default=None, max_length=2048)
    product_url: Optional[str] = Field(default=None, max_length=2048)
    competitor_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    asins: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    auto_discover_competitors: bool = Field(default=False)
    market_totals_note: Optional[str] = Field(default=None, max_length=1024)

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Listing(SQLModel, table=True):
    __tablename__ = "listing"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="job.id", index=True)
    asin: str = Field(max_length=32, index=True)
    title: str = Field(default="", max_length=2048)
    price: Optional[float] = Field(default=None)
    currency: str = Field(default="USD", max_length=8)
    bsr_rank: Optional[int] = Field(default=None)
    bsr_category: Optional[str] = Field(default=None, max_length=512)
    avg_rating: Optional[float] = Field(default=None)
    review_count: Optional[int] = Field(default=None)
    canonical_url: Optional[str] = Field(default=None, max_length=2048)
    estimated_monthly_units: Optional[float] = Field(default=None)
    # Always denominated in INR; see services/revenue.py.
    estimated_monthly_revenue: Optional[float] = Field(default=None)
    # Lower-bound monthly sales pulled from Amazon's "X bought in past month" badge.
    previous_month_units: Optional[int] = Field(default=None)
    # "bought_past_month" | "bsr_heuristic" | "unknown"
    revenue_basis: str = Field(default="unknown", max_length=32)
    # Listing.price normalized to INR using the FX rate captured at job time.
    unit_price_inr: Optional[float] = Field(default=None)
    # Browse path / JSON-LD category when BSR category is missing (e.g. "Home & Kitchen › Mosquito Nets").
    product_category: Optional[str] = Field(default=None, max_length=512)

    captured_at: datetime = Field(default_factory=utc_now)
    raw_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class Review(SQLModel, table=True):
    __tablename__ = "review"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="job.id", index=True)
    asin: str = Field(max_length=32, index=True)
    external_id: str = Field(max_length=128, index=True)
    rating: Optional[int] = Field(default=None)
    title: Optional[str] = Field(default=None, max_length=512)
    body: str = Field(default="", max_length=8192)
    review_date: Optional[str] = Field(default=None, max_length=32)
    is_verified_purchase: bool = Field(default=False)
    has_customer_images: bool = Field(default=False)

    created_at: datetime = Field(default_factory=utc_now)


class Summary(SQLModel, table=True):
    __tablename__ = "summary"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="job.id", index=True)
    asin: str = Field(max_length=32, index=True)
    map_batches: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    final_summary: str = Field(default="", max_length=65000)
    key_purchase_criteria: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    why_buyers_like: Optional[str] = Field(default=None, max_length=16000)
    why_buyers_caution: Optional[str] = Field(default=None, max_length=16000)

    created_at: datetime = Field(default_factory=utc_now)
