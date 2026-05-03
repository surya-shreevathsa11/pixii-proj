from __future__ import annotations

import asyncio
import traceback
import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.config import settings
from app.database import engine
from app.models import Job, JobFlow, JobStatus, Listing, Review, Summary
from app.services.scraping.base import NormalizedReview
from app.services.llm_review import (
    batch_map_review_themes,
    format_review_batches,
    reduce_review_map,
    synthesize_reviews_single_pass,
)
from app.services.revenue import (
    compute_revenue_inr,
    convert_to_inr,
    estimate_monthly_units_from_bsr,
    get_usd_inr_rate,
)
from app.services.scraping.factory import get_scraping_provider
from app.services.scraping.util import amazon_domain_from_url, normalize_amazon_domain


def utc_now():
    return datetime.now(timezone.utc)


def persist_job_touch(session: Session, job: Job) -> None:
    job.updated_at = utc_now()
    session.add(job)
    session.commit()
    session.refresh(job)


async def ingest_reviews(
    provider,
    amazon_domain: str,
    job_id: uuid.UUID,
    asin: str,
    session: Session,
    flow: JobFlow,
) -> int:
    """Persist reviews for one ASIN.

    * **Market**: keep up to ``max_reviews_per_asin``; if ``reviews_only_with_customer_images`` is true,
      skip reviews without customer photos (legacy behavior).
    * **Competitive**: fetch up to ``competitive_review_fetch_buffer`` recent rows, sort by
      ``(-has_customer_images, arrival_index)`` so images are preferred among equally recent rows,
      then persist only ``competitive_reviews_per_asin`` (including text-only reviews to fill the cap).
    """
    if flow == JobFlow.competitive:
        return await _ingest_reviews_competitive(provider, amazon_domain, job_id, asin, session)
    return await _ingest_reviews_market(provider, amazon_domain, job_id, asin, session)


async def _ingest_reviews_market(provider, amazon_domain: str, job_id: uuid.UUID, asin: str, session: Session) -> int:
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
            if settings.reviews_only_with_customer_images and not getattr(rv, "has_customer_images", False):
                continue

            stmt = (
                select(Review)
                .where(Review.job_id == job_id)
                .where(Review.asin == asin)
                .where(Review.external_id == rv.external_id)
            )
            if session.exec(stmt).first():
                continue

            body = (rv.body or "").strip()

            session.add(
                Review(
                    job_id=job_id,
                    asin=asin.upper(),
                    external_id=rv.external_id,
                    rating=rv.rating,
                    title=rv.title,
                    body=body[:8192],
                    review_date=rv.review_date,
                    is_verified_purchase=rv.is_verified_purchase,
                    has_customer_images=bool(getattr(rv, "has_customer_images", False)),
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


async def _ingest_reviews_competitive(
    provider, amazon_domain: str, job_id: uuid.UUID, asin: str, session: Session,
) -> int:
    target = max(1, settings.competitive_reviews_per_asin)
    buffer_cap = max(target, settings.competitive_review_fetch_buffer)
    buffer: list[tuple[int, NormalizedReview]] = []
    seq = 0
    page = 1
    empty_pages = 0

    while len(buffer) < buffer_cap:
        rows, next_token = await provider.fetch_reviews_page(asin, amazon_domain, str(page))
        if not rows:
            empty_pages += 1
            if empty_pages >= 4:
                break
            page += 1
            continue

        empty_pages = 0
        seen_ids = {rv.external_id for _, rv in buffer}
        for rv in rows:
            if rv.external_id in seen_ids:
                continue
            buffer.append((seq, rv))
            seq += 1
            seen_ids.add(rv.external_id)
            if len(buffer) >= buffer_cap:
                break

        session.commit()

        if len(buffer) >= buffer_cap:
            break
        if next_token and next_token.isdigit():
            page = max(1, int(next_token))
        else:
            page += 1
        if page > 40:
            break

    def _rv_sort_key(item: tuple[int, NormalizedReview]) -> tuple[int, float, int]:
        seq, rv = item
        ts = 0.0
        raw = (rv.review_date or "").strip()[:48]
        if raw:
            for fmt in ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ts = dt.timestamp()
                    break
                except ValueError:
                    continue
        has_img = int(bool(getattr(rv, "has_customer_images", False)))
        return (has_img, ts, -seq)

    buffer.sort(key=_rv_sort_key, reverse=True)
    chosen = buffer[:target]

    persisted = 0
    for _idx, rv in chosen:
        stmt = (
            select(Review)
            .where(Review.job_id == job_id)
            .where(Review.asin == asin)
            .where(Review.external_id == rv.external_id)
        )
        if session.exec(stmt).first():
            continue

        body = (rv.body or "").strip()
        has_img = bool(getattr(rv, "has_customer_images", False))

        session.add(
            Review(
                job_id=job_id,
                asin=asin.upper(),
                external_id=rv.external_id,
                rating=rv.rating,
                title=rv.title,
                body=body[:8192],
                review_date=rv.review_date,
                is_verified_purchase=rv.is_verified_purchase,
                has_customer_images=has_img,
            )
        )
        persisted += 1

    session.commit()
    return persisted


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
        hint = "enable reviews API or widen limits."
        if job.flow != JobFlow.competitive and settings.reviews_only_with_customer_images:
            hint += (
                " With REVIEWS_ONLY_WITH_CUSTOMER_IMAGES enabled, only reviews that include customer photos are kept. "
                "try SCRAPERAPI_RENDER=true, or set REVIEWS_ONLY_WITH_CUSTOMER_IMAGES=false to include all text reviews."
            )
        elif job.flow == JobFlow.competitive:
            hint += (
                " Competitive analyses keep up to ten recent reviews per ASIN. "
                "On amazon.in, review HTML is often client-rendered: set SCRAPERAPI_RENDER=true (or rely on the "
                "server’s one-shot render fallback when structured reviews are empty) and ensure AMAZON_DOMAIN matches the storefront."
            )
        stub = Summary(
            job_id=job.id,
            asin=asin,
            map_batches=[],
            final_summary=f"No synced reviews captured for {asin}; {hint}",
            key_purchase_criteria=[],
        )
        session.add(stub)
        session.commit()
        return

    competitive_cap = max(1, settings.competitive_reviews_per_asin)
    use_single_pass = job.flow == JobFlow.competitive and len(reviews_rows) <= competitive_cap

    if use_single_pass:
        lines: list[str] = []
        for r in reviews_rows:
            rr = r.rating if r.rating is not None else "?"
            safe = (r.body or "").replace("\n", " ").strip()
            lines.append(f"Rating {rr}: {safe}")
        synth = await synthesize_reviews_single_pass(asin, product_title or "", lines)
        summary_row = Summary(
            job_id=job.id,
            asin=asin,
            map_batches=[],
            final_summary=synth.final_summary[:65000],
            key_purchase_criteria=synth.key_purchase_criteria,
            why_buyers_like=(synth.why_buyers_like or "")[:16000] or None,
            why_buyers_caution=(synth.why_buyers_caution or "")[:16000] or None,
        )
    else:
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
    default_amazon_domain = normalize_amazon_domain(settings.amazon_domain)

    try:
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return

            # Prefer the storefront host from the job's URL (e.g. amazon.in) over the global default.
            inferred = amazon_domain_from_url(job.bestsellers_url or job.product_url or "")
            amazon_domain = inferred or default_amazon_domain

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

                if job.auto_discover_competitors:
                    primary = target_asins[0]
                    job.phase = "Discovering similar ASINs"
                    persist_job_touch(session, job)
                    extras = await provider.discover_competitor_asins(primary, amazon_domain, 9)
                    if not extras:
                        raise ValueError(
                            "Auto-discover found no related ASINs on the product page. "
                            "Try SCRAPERAPI_RENDER=true, paste competitor URLs manually, or send auto_discover_competitors=false."
                        )
                    merged: list[str] = []
                    for a in [primary, *extras]:
                        ua = a.upper()
                        if ua not in merged:
                            merged.append(ua)
                        if len(merged) >= 10:
                            break
                    target_asins = merged
                    job.asins = target_asins
                    persist_job_touch(session, job)

            job.phase = "Listing metadata ingest"
            persist_job_touch(session, job)

            total = len(target_asins)
            # Resolve the FX rate once per job so every listing's revenue uses the same INR conversion.
            fx_rate = await get_usd_inr_rate()

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
                row.product_category = nl.product_category
                row.avg_rating = nl.avg_rating
                row.review_count = nl.review_count
                row.canonical_url = nl.canonical_url
                row.captured_at = utc_now()
                row.previous_month_units = nl.previous_month_units
                row.unit_price_inr = convert_to_inr(nl.price, nl.currency, fx_rate)

                est = estimate_monthly_units_from_bsr(nl.bsr_rank, nl.bsr_category)
                bsr_units = float(est.monthly_units) if est else None

                revenue = compute_revenue_inr(
                    previous_month_units=nl.previous_month_units,
                    bsr_units=bsr_units,
                    unit_price=nl.price,
                    currency=nl.currency,
                    usd_inr_rate=fx_rate,
                )

                if revenue.basis == "bought_past_month":
                    row.estimated_monthly_units = float(nl.previous_month_units or 0)
                elif est:
                    row.estimated_monthly_units = float(est.monthly_units)
                row.estimated_monthly_revenue = revenue.amount_inr
                row.revenue_basis = revenue.basis

                raw_payload = nl.raw if isinstance(nl.raw, dict) else {"payload": nl.raw}
                raw_payload = dict(raw_payload)
                raw_payload["revenue_basis"] = revenue.basis
                raw_payload["revenue_rationale"] = revenue.rationale
                raw_payload["fx_usd_inr"] = fx_rate
                if nl.previous_month_label:
                    raw_payload["previous_month_label"] = nl.previous_month_label
                row.raw_metadata = raw_payload

                session.add(row)
                job.phase = f"Fetched listing ({idx}/{total or 1}): {asin}"
                persist_job_touch(session, job)

            refreshed = session.exec(select(Listing).where(Listing.job_id == job.id)).all()

            listing_total_rev = sum((l.estimated_monthly_revenue or 0) for l in refreshed)
            extrapolation = (
                "Top-visible slice extrapolation assumption: multiplying the summed sampled revenue x3 hints at "
                f"roughly INR {listing_total_rev * 3:,.0f}/month for illustrative planning only."
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
                    await ingest_reviews(provider, amazon_domain, job.id, asin, session, job.flow)

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