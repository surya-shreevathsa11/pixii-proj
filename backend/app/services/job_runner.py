from __future__ import annotations

import asyncio
import traceback
import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.config import settings
from app.database import engine
from app.models import Job, JobFlow, JobStatus, Listing, Review, Summary
from app.services.llm_review import batch_map_review_themes, format_review_batches, reduce_review_map
from app.services.revenue import estimate_monthly_units_from_bsr, monthly_revenue_from_units
from app.services.scraping.factory import get_scraping_provider
from app.services.scraping.util import normalize_amazon_domain


def utc_now():
    return datetime.now(timezone.utc)


def persist_job_touch(session: Session, job: Job) -> None:
    job.updated_at = utc_now()
    session.add(job)
    session.commit()
    session.refresh(job)


async def ingest_reviews(provider, amazon_domain: str, job_id: uuid.UUID, asin: str, session: Session) -> int:
    page = 1
    collected = 0
    empty_pages = 0

    while collected < settings.max_reviews_per_asin:
        rows, next_token = await provider.fetch_reviews_page(asin, amazon_domain, str(page))
        if not rows:
            empty_pages += 1
            if empty_pages >= 2:
                break
            page += 1
            continue

        empty_pages = 0

        for rv in rows:
            stmt = (
                select(Review)
                .where(Review.job_id == job_id)
                .where(Review.asin == asin)
                .where(Review.external_id == rv.external_id)
            )
            if session.exec(stmt).first():
                continue
            session.add(
                Review(
                    job_id=job_id,
                    asin=asin.upper(),
                    external_id=rv.external_id,
                    rating=rv.rating,
                    title=rv.title,
                    body=rv.body,
                    review_date=rv.review_date,
                    is_verified_purchase=rv.is_verified_purchase,
                )
            )
            collected += 1
            if collected >= settings.max_reviews_per_asin:
                break

        session.commit()

        if collected >= settings.max_reviews_per_asin:
            break

        if next_token and next_token.isdigit():
            page = max(1, int(next_token))
        else:
            page += 1

        if page > 260:
            break

    return collected


async def summarize_asin(job: Job, asin: str, session: Session) -> None:
    if session.exec(
        select(Summary).where(Summary.job_id == job.id).where(Summary.asin == asin)
    ).first():
        return

    listing_row = session.exec(
        select(Listing).where(Listing.job_id == job.id).where(Listing.asin == asin)
    ).first()
    product_title = listing_row.title if listing_row else ""

    reviews_rows = session.exec(
        select(Review).where(Review.job_id == job.id).where(Review.asin == asin)
    ).all()

    if not reviews_rows:
        stub = Summary(
            job_id=job.id,
            asin=asin,
            map_batches=[],
            final_summary=f"No synced reviews captured for {asin}; enable reviews API or widen limits.",
            key_purchase_criteria=[],
        )
        session.add(stub)
        session.commit()
        return

    bodies = [r.body or "" for r in reviews_rows]
    ratings = [r.rating for r in reviews_rows]
    batches_txt = format_review_batches(bodies, ratings, settings.review_batch_map_size)

    map_batches: list[dict] = []
    for batch in batches_txt:
        if not batch:
            continue
        line_idx = len(map_batches)
        formatted = [f"[{asin}-map{line_idx}] {line}" for line in batch]
        themed = await batch_map_review_themes(product_title or asin, formatted)
        map_batches.append(themed)

    final_summary, kp = await reduce_review_map(asin, product_title or "", map_batches)

    summary_row = Summary(
        job_id=job.id,
        asin=asin,
        map_batches=map_batches,
        final_summary=final_summary[:65000],
        key_purchase_criteria=kp,
    )
    session.add(summary_row)
    session.commit()


async def orchestrate(job_id: uuid.UUID) -> None:
    provider = get_scraping_provider()
    amazon_domain = normalize_amazon_domain(settings.amazon_domain)

    try:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return

            job.status = JobStatus.running
            job.phase = "Initializing"
            job.error_message = None
            persist_job_touch(session, job)

            target_asins: list[str]

            if job.flow == JobFlow.market:
                if not job.bestsellers_url:
                    raise ValueError("Missing bestsellers_url for market job")

                job.phase = "Resolving bestseller ASINs"
                persist_job_touch(session, job)

                discovered = await provider.fetch_best_seller_asins(job.bestsellers_url, amazon_domain, 12)
                if not discovered:
                    raise ValueError("No ASINs returned for bestsellers URL; verify provider + URL")

                target_asins = [a.upper() for a in discovered[:10]]
                job.asins = target_asins
                persist_job_touch(session, job)
            else:
                target_asins = [a.upper() for a in job.asins]
                if not target_asins:
                    raise ValueError("No ASINs supplied for competitive job")

            job.phase = "Listing metadata ingest"
            persist_job_touch(session, job)

            total = len(target_asins)

            for idx, asin in enumerate(target_asins, start=1):
                nl = await provider.fetch_listing(asin, amazon_domain)

                stmt = (
                    select(Listing)
                    .where(Listing.job_id == job.id)
                    .where(Listing.asin == asin)
                )
                row = session.exec(stmt).first()
                if row is None:
                    row = Listing(job_id=job.id, asin=asin)

                row.title = nl.title or row.title or ""
                row.price = nl.price
                row.currency = nl.currency or "USD"
                row.bsr_rank = nl.bsr_rank
                row.bsr_category = nl.bsr_category
                row.avg_rating = nl.avg_rating
                row.review_count = nl.review_count
                row.canonical_url = nl.canonical_url
                row.captured_at = utc_now()
                row.raw_metadata = nl.raw if isinstance(nl.raw, dict) else {"payload": nl.raw}

                est = estimate_monthly_units_from_bsr(nl.bsr_rank, nl.bsr_category)
                if est:
                    row.estimated_monthly_units = float(est.monthly_units)
                    row.estimated_monthly_revenue = monthly_revenue_from_units(est.monthly_units, nl.price)

                session.add(row)
                job.phase = f"Fetched listing ({idx}/{total or 1}): {asin}"
                persist_job_touch(session, job)

            refreshed = session.exec(select(Listing).where(Listing.job_id == job.id)).all()

            listing_total_rev = sum((l.estimated_monthly_revenue or 0) for l in refreshed)
            extrapolation = (
                "Top-visible slice extrapolation assumption: multiplying the summed sampled revenue ×3 hints at "
                f"roughly USD {listing_total_rev * 3:,.2f}/month for illustrative planning only."
                if refreshed
                else ""
            )

            job.phase = "Revenue rollup"
            persist_job_touch(session, job)

            if job.flow == JobFlow.market:
                note = extrapolation[:1024] if extrapolation else ""
                fallback = (
                    "Market sizing note: connect a scraping provider for live ASIN telemetry; "
                    "mock mode fabricates illustrative ranks."
                )
                job.market_totals_note = note or fallback[:1024]
                persist_job_touch(session, job)

            if job.flow == JobFlow.competitive:
                job.phase = "Review ingestion"
                persist_job_touch(session, job)

                for asin_idx, asin in enumerate(target_asins, start=1):
                    job.phase = f"Harvesting reviews ({asin_idx}/{total or 1}): {asin}"
                    persist_job_touch(session, job)
                    await ingest_reviews(provider, amazon_domain, job.id, asin, session)

                job.phase = "LLM aggregation"
                persist_job_touch(session, job)

                for sidx, asin in enumerate(target_asins, start=1):
                    job.phase = f"Summarizing reviews ({sidx}/{total or 1})"
                    persist_job_touch(session, job)
                    await summarize_asin(job, asin, session)

            job.phase = "Done"
            job.status = JobStatus.completed
            persist_job_touch(session, job)

    except Exception as exc:
        with Session(engine) as session_err:
            fail_job = session_err.get(Job, job_id)
            if fail_job:
                fail_job.status = JobStatus.failed
                fail_job.phase = "Failed"
                fail_job.error_message = f"{exc}\n{traceback.format_exc()}"
                fail_job.updated_at = utc_now()
                session_err.add(fail_job)
                session_err.commit()


def run_job_task(job_id: uuid.UUID) -> None:
    asyncio.run(orchestrate(job_id))