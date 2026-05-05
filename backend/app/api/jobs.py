import logging
import re
import uuid
from collections import defaultdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Job, JobFlow, JobStatus, Listing, Review, Summary
from app.schemas import (
    CompetitiveJobCreate,
    JobCreateResponse,
    JobDetailResponse,
    ListingOut,
    MarketJobCreate,
    ReviewOut,
    SummaryOut,
    YouTubeInsightsOut,
)
from app.services.job_runner import run_job_task
from app.services.scraping.util import (
    amazon_domain_from_url,
    extract_asin_from_amazon_url,
    normalize_amazon_domain,
)

router = APIRouter(tags=["jobs"])
logger = logging.getLogger(__name__)

_REVIEW_BODY_LEGACY_PREFIX = "[Customer photos in review]"
_AMAZON_BESTSELLERS_PATH_RX = re.compile(r"/(?:gp/bestsellers|best-sellers)\b", re.I)


def _review_body_for_api(text: str | None, cap: int = 2000) -> str:
    b = (text or "").strip()
    if b.startswith(_REVIEW_BODY_LEGACY_PREFIX):
        b = b[len(_REVIEW_BODY_LEGACY_PREFIX) :].lstrip()
    return b[:cap]


def resolve_competitive_asins(product_url: str, competitor_urls: list[str], auto_discover: bool) -> list[str]:
    mine = extract_asin_from_amazon_url(product_url)
    if mine is None:
        # Competitive flow should not hard-fail on arbitrary/non-Amazon links.
        # Return empty and let enqueue endpoint short-circuit without triggering scraper jobs.
        return []

    if auto_discover:
        return [mine.upper()]

    output: list[str] = []
    seen: set[str] = {mine.upper()}

    for idx, rival in enumerate(competitor_urls):
        trimmed = rival.strip()
        if not trimmed:
            continue

        rival_asin = extract_asin_from_amazon_url(trimmed)
        if rival_asin is None:
            # Ignore invalid competitor links to avoid random hard failures/costly retries.
            continue
        ua = rival_asin.upper()
        if ua not in seen:
            seen.add(ua)
            output.append(ua)

        if len(output) >= 9:
            break

    return [mine.upper(), *output[:9]]


def _resolved_job_amazon_domain(job: Job) -> str:
    inferred = amazon_domain_from_url(job.bestsellers_url or job.product_url or "")
    return inferred or normalize_amazon_domain(settings.amazon_domain)


def _ingest_demo_from_listings(listings_rows: list[Listing]) -> bool:
    """True when listings are mock-derived, or (before any listing) the server uses mock scraping."""
    if not listings_rows:
        p = settings.scraping_provider.lower().strip()
        return p not in {"scraperapi", "scraper_api"}

    for row in listings_rows:
        meta = row.raw_metadata if isinstance(row.raw_metadata, dict) else {}
        if meta.get("demo") is True:
            return True
    return False


def build_job_detail(session: Session, job: Job) -> JobDetailResponse:
    listings_rows = session.exec(select(Listing).where(Listing.job_id == job.id)).all()

    summaries_rows = session.exec(select(Summary).where(Summary.job_id == job.id)).all()

    reviews_total = session.exec(select(Review.id).where(Review.job_id == job.id)).all()

    listings_out = [
        ListingOut(
            asin=row.asin,
            title=row.title or "",
            price=row.price,
            currency=row.currency,
            bsr_rank=row.bsr_rank,
            bsr_category=row.bsr_category,
            product_category=row.product_category,
            avg_rating=row.avg_rating,
            review_count=row.review_count,
            canonical_url=row.canonical_url,
            estimated_monthly_units=row.estimated_monthly_units,
            estimated_monthly_revenue=row.estimated_monthly_revenue,
            previous_month_units=row.previous_month_units,
            revenue_basis=row.revenue_basis or "unknown",
            unit_price_inr=row.unit_price_inr,
        )
        for row in listings_rows
    ]

    titles_by_asin = {row.asin: (row.title or "").strip() for row in listings_rows}

    summaries_out = [
        SummaryOut(
            asin=row.asin,
            product_title=titles_by_asin.get(row.asin, ""),
            final_summary=row.final_summary,
            key_purchase_criteria=row.key_purchase_criteria or [],
            why_buyers_like=row.why_buyers_like,
            why_buyers_caution=row.why_buyers_caution,
        )
        for row in summaries_rows
    ]

    per_asin_cap = max(10, settings.competitive_reviews_per_asin)
    reviews_rows = session.exec(
        select(Review).where(Review.job_id == job.id).order_by(Review.created_at.desc())
    ).all()
    picked_by_asin: dict[str, list[Review]] = defaultdict(list)
    for rv in reviews_rows:
        if len(picked_by_asin[rv.asin]) >= per_asin_cap:
            continue
        picked_by_asin[rv.asin].append(rv)

    reviews_out: list[ReviewOut] = []
    for asin_key in sorted(picked_by_asin.keys()):
        for rv in reversed(picked_by_asin[asin_key]):
            snippet = _review_body_for_api(rv.body, 2000)
            reviews_out.append(
                ReviewOut(
                    asin=rv.asin,
                    rating=rv.rating,
                    title=rv.title,
                    body=snippet,
                    review_date=rv.review_date,
                    has_customer_images=bool(rv.has_customer_images),
                    verified=bool(rv.is_verified_purchase),
                )
            )

    competitor_urls = job.competitor_urls or []

    ingest_demo = _ingest_demo_from_listings(list(listings_rows))
    claude_configured = bool(settings.anthropic_api_key.strip())
    youtube_configured = bool(
        settings.youtube_data_api_key.strip() or settings.youtube_data_fallback_api_key.strip()
    )

    raw_yt = getattr(job, "youtube_insights", None)
    youtube_insights_out: YouTubeInsightsOut | None = None
    if isinstance(raw_yt, dict) and raw_yt:
        try:
            youtube_insights_out = YouTubeInsightsOut.model_validate(raw_yt)
        except Exception:
            logger.warning("Invalid youtube_insights JSON for job %s", job.id)

    return JobDetailResponse(
        id=job.id,
        flow=job.flow,
        status=job.status,
        phase=job.phase or "",
        error_message=job.error_message,
        amazon_domain=_resolved_job_amazon_domain(job),
        bestsellers_url=job.bestsellers_url,
        product_url=job.product_url,
        competitor_urls=competitor_urls,
        asins=list(job.asins or []),
        market_totals_note=job.market_totals_note,
        listings=sorted(listings_out, key=lambda listing: -(listing.estimated_monthly_revenue or 0.0)),
        summaries=sorted(summaries_out, key=lambda sm: sm.asin),
        reviews=reviews_out,
        reviews_count_total=len(reviews_total),
        created_at=job.created_at,
        ingest_demo=ingest_demo,
        claude_configured=claude_configured,
        youtube_configured=youtube_configured,
        youtube_insights=youtube_insights_out,
    )


@router.post("/jobs/competitive", response_model=JobCreateResponse)
def enqueue_competitive_job(
    payload: CompetitiveJobCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    resolved = resolve_competitive_asins(
        payload.product_url,
        payload.competitor_urls,
        payload.auto_discover_competitors,
    )

    if not resolved:
        job = Job(
            flow=JobFlow.competitive,
            status=JobStatus.completed,
            phase="Skipped: input is not a resolvable Amazon product link",
            product_url=payload.product_url.strip(),
            competitor_urls=list(payload.competitor_urls),
            asins=[],
            auto_discover_competitors=payload.auto_discover_competitors,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return JobCreateResponse(job_id=job.id)

    job = Job(
        flow=JobFlow.competitive,
        status=JobStatus.queued,
        phase="Queued",
        product_url=payload.product_url.strip(),
        competitor_urls=list(payload.competitor_urls),
        asins=list(resolved),
        auto_discover_competitors=payload.auto_discover_competitors,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(run_job_task, job.id)
    return JobCreateResponse(job_id=job.id)


@router.post("/jobs/market", response_model=JobCreateResponse)
def enqueue_market_job(
    payload: MarketJobCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    raw = payload.bestsellers_url.strip()
    host = amazon_domain_from_url(raw)
    if not host or not _AMAZON_BESTSELLERS_PATH_RX.search(raw):
        raise HTTPException(
            status_code=400,
            detail="Please enter an Amazon Best Sellers page link (for example: amazon.in/gp/bestsellers/...).",
        )

    job = Job(
        flow=JobFlow.market,
        status=JobStatus.queued,
        phase="Queued",
        bestsellers_url=raw,
        asins=[],
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(run_job_task, job.id)
    return JobCreateResponse(job_id=job.id)


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
def get_job_detail(job_id: uuid.UUID, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return build_job_detail(session, job)
