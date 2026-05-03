import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Job, JobFlow, JobStatus, Listing, Review, Summary
from app.schemas import CompetitiveJobCreate, JobCreateResponse, JobDetailResponse, ListingOut, MarketJobCreate, SummaryOut
from app.services.job_runner import run_job_task
from app.services.scraping.util import extract_asin_from_amazon_url

router = APIRouter(tags=["jobs"])


def resolve_competitive_asins(product_url: str, competitor_urls: list[str], auto_discover: bool) -> list[str]:
    mine = extract_asin_from_amazon_url(product_url)
    if mine is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not resolve ASIN from product_url. Use a full amazon storefront URL with /dp/ASIN, "
                "a bare 10-character ASIN, or a short link (amzn.in / amzn.to) that this server can open over HTTP."
            ),
        )

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
            raise HTTPException(status_code=400, detail=f"Unable to derive ASIN from competitor URL #{idx + 1}")
        ua = rival_asin.upper()
        if ua not in seen:
            seen.add(ua)
            output.append(ua)

        if len(output) >= 9:
            break

    return [mine.upper(), *output[:9]]


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
        )
        for row in summaries_rows
    ]

    competitor_urls = job.competitor_urls or []

    ingest_demo = _ingest_demo_from_listings(list(listings_rows))
    gemini_configured = bool(settings.google_api_key.strip())

    return JobDetailResponse(
        id=job.id,
        flow=job.flow,
        status=job.status,
        phase=job.phase or "",
        error_message=job.error_message,
        bestsellers_url=job.bestsellers_url,
        product_url=job.product_url,
        competitor_urls=competitor_urls,
        asins=list(job.asins or []),
        market_totals_note=job.market_totals_note,
        listings=sorted(listings_out, key=lambda listing: -(listing.estimated_monthly_revenue or 0.0)),
        summaries=sorted(summaries_out, key=lambda sm: sm.asin),
        reviews_count_total=len(reviews_total),
        created_at=job.created_at,
        ingest_demo=ingest_demo,
        gemini_configured=gemini_configured,
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
    job = Job(
        flow=JobFlow.market,
        status=JobStatus.queued,
        phase="Queued",
        bestsellers_url=payload.bestsellers_url.strip(),
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
