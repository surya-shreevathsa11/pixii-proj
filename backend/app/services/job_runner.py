from __future__ import annotations

import asyncio
import logging
import math
import traceback
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.config import settings
from app.database import engine
from app.models import Job, JobFlow, JobStatus, Listing, Review, Summary
from app.services.scraping.base import NormalizedListing, NormalizedReview
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
from app.services.comparison_spec import ComparisonSpec, infer_comparison_spec
from app.services.youtube_intel import enrich_competitive_job_youtube_insights
from app.services.scraping.factory import get_scraping_provider
from app.services.scraping.scraperapi import (
    _ACCESSORY_TILE_HINT,
    _HANDSET_PRIMARY_HINT,
    _SERVICE_CATEGORY_HINT,
    _UNIVERSAL_SERVICE_TITLE_HINT,
    _category_leaf_tokens,
    _title_fingerprint,
)
from app.services.scraping.util import amazon_domain_from_url, normalize_amazon_domain

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


def _is_universal_service_listing(title: str, bsr_category: str | None, product_category: str | None) -> bool:
    """Return True if a listing is a service/plan/warranty/subscription rather than a physical product.

    Product-agnostic. Applied to every competitive job regardless of primary type.
    """
    if _UNIVERSAL_SERVICE_TITLE_HINT.search(title or ""):
        return True
    for cat in (bsr_category, product_category):
        if cat and _SERVICE_CATEGORY_HINT.search(cat):
            return True
    return False


def _categories_compatible(
    primary_bsr: str | None,
    primary_cat: str | None,
    competitor_bsr: str | None,
    competitor_cat: str | None,
) -> bool:
    """Return True when primary & competitor share at least one meaningful category-leaf token.

    Falls back to True (permissive) when either side lacks usable category text, so we never
    drop competitors purely because the scraper failed to extract a category.
    """
    primary_tokens: set[str] = set()
    primary_tokens.update(_category_leaf_tokens(primary_bsr))
    primary_tokens.update(_category_leaf_tokens(primary_cat))

    competitor_tokens: set[str] = set()
    competitor_tokens.update(_category_leaf_tokens(competitor_bsr))
    competitor_tokens.update(_category_leaf_tokens(competitor_cat))

    if not primary_tokens or not competitor_tokens:
        return True
    return bool(primary_tokens & competitor_tokens)


def _is_service_or_accessory_listing(title: str, bsr_category: str | None, product_category: str | None) -> bool:
    """Legacy handset-specific accessory filter, kept for the handset path."""
    if _ACCESSORY_TILE_HINT.search(title or ""):
        return True
    for cat in (bsr_category, product_category):
        if cat and _SERVICE_CATEGORY_HINT.search(cat):
            return True
    return False


def _is_thin_listing(nl: "NormalizedListing") -> bool:
    """A scrape so empty it would render as 'Amazon product B0XXX' with all-N/A fields.

    Triggered by ScraperAPI 4xx→empty body, Amazon stubs without #productTitle, or any
    other path where neither title nor price nor BSR could be parsed. Also honors the
    explicit ``raw['parse_thin']`` flag set by the scraper.
    """
    raw = nl.raw if isinstance(nl.raw, dict) else {}
    if raw.get("parse_thin"):
        return True
    title = (nl.title or "").strip()
    title_l = title.lower()
    placeholder = (
        not title
        or title_l.startswith("amazon product ")
        or title_l.startswith("(blocked?) ")
    )
    return placeholder and nl.price is None and nl.bsr_rank is None


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


def _review_row_has_meaningful_text(r: Review) -> bool:
    """True when title or body has enough characters for Claude to quote themes."""
    text = f"{(r.title or '').strip()} {(r.body or '').strip()}".strip()
    return len(text) >= 12


def _fallback_kpc_from_reviews_rows(reviews_rows: list[Review], limit: int = 8) -> list[str]:
    """Deterministic KPC fallback from scraped review text when LLM output is unavailable."""
    picks: list[str] = []
    seen: set[str] = set()
    for r in reviews_rows:
        raw = f"{(r.title or '').strip()} {(r.body or '').strip()}".strip()
        if len(raw) < 18:
            continue
        text = " ".join(raw.split())
        snippet = text[:140].rstrip(" ,.;:")
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        picks.append(snippet)
        if len(picks) >= limit:
            break
    return picks


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
            raw = f"{(r.title or '').strip()} {(r.body or '').strip()}".strip()
            safe = raw.replace("\n", " ").strip()
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
        if not any(_review_row_has_meaningful_text(r) for r in reviews_rows):
            stub = Summary(
                job_id=job.id,
                asin=asin,
                map_batches=[],
                final_summary=(
                    f"Synced {len(reviews_rows)} review row(s) for {asin}, but none contained usable text "
                    "(empty titles and bodies—often a scrape or render issue). Skipped Claude to save quota. "
                    "Try SCRAPERAPI_RENDER=true and re-run."
                ),
                key_purchase_criteria=[
                    "Re-run after fixing review-text capture; KPC needs actual shopper sentences.",
                ],
            )
            session.add(stub)
            session.commit()
            return

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
        if not kp:
            kp = _fallback_kpc_from_reviews_rows(reviews_rows)
        if not kp:
            kp = ["Re-run after fixing review-text capture; KPC needs actual shopper sentences."]

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

            # Fetch primary listing up-front for competitive jobs so the comparison spec
            # (Claude-driven) and downstream filters use the canonical PDP title/category.
            # Cached to avoid re-fetching during pass 1.
            primary_nl_cache: NormalizedListing | None = None
            primary_title_for_filter: str = ""
            primary_bsr_category: str | None = None
            primary_product_category: str | None = None
            comparison_spec: ComparisonSpec | None = None

            if job.flow == JobFlow.competitive and target_asins:
                job.phase = "Fetching primary product"
                persist_job_touch(session, job)
                try:
                    primary_nl_cache = await provider.fetch_listing(target_asins[0], amazon_domain)
                    primary_title_for_filter = (primary_nl_cache.title or "").strip()
                    primary_bsr_category = primary_nl_cache.bsr_category
                    primary_product_category = primary_nl_cache.product_category
                except Exception as exc:
                    logger.warning(
                        "Primary fetch_listing failed for %s on %s (%s); discovery will proceed without title/category context.",
                        target_asins[0], amazon_domain, exc,
                    )
                    primary_nl_cache = None
                    primary_title_for_filter = ""

                if primary_title_for_filter:
                    job.phase = "Inferring comparison spec"
                    persist_job_touch(session, job)
                    try:
                        comparison_spec = await infer_comparison_spec(
                            primary_title_for_filter,
                            primary_bsr_category or primary_product_category,
                        )
                    except Exception as exc:
                        logger.warning(
                            "infer_comparison_spec failed for %s (%s); falling back to heuristic discovery.",
                            target_asins[0], exc,
                        )
                        comparison_spec = None

            if job.flow == JobFlow.competitive:
                if job.auto_discover_competitors:
                    primary = target_asins[0]
                    job.phase = "Discovering similar ASINs"
                    persist_job_touch(session, job)
                    pool_lim = max(9, settings.competitive_discovery_pool_limit)
                    extras: list[str] = []
                    try:
                        extras = await provider.discover_competitor_asins(
                            primary,
                            amazon_domain,
                            9,
                            candidate_pool_limit=pool_lim,
                            spec=comparison_spec,
                        )
                    except Exception as exc:
                        # ScraperAPI 499 / 5xx / network blip: degrade gracefully to primary-only analysis
                        # instead of crashing the whole job. The user still gets listings + reviews + summary.
                        logger.warning(
                            "Auto-discover failed for %s on %s (%s); falling back to primary-only competitive run.",
                            primary, amazon_domain, exc,
                        )
                        extras = []

                    if extras:
                        merged: list[str] = []
                        max_asins = 1 + pool_lim
                        for a in [primary, *extras]:
                            ua = a.upper()
                            if ua not in merged:
                                merged.append(ua)
                            if len(merged) >= max_asins:
                                break
                        target_asins = merged
                    else:
                        # Keep the primary so downstream phases still produce useful output.
                        job.error_message = (
                            "Auto-discover returned no peers (ScraperAPI block / proxy issue likely). "
                            "Continuing with primary ASIN only; rerun later or paste competitor URLs manually."
                        )
                    job.asins = target_asins
                    persist_job_touch(session, job)

            job.phase = "Listing metadata ingest"
            persist_job_touch(session, job)

            total = len(target_asins)
            # Resolve the FX rate once per job so every listing's revenue uses the same INR conversion.
            fx_rate = await get_usd_inr_rate()

            primary_is_handset = bool(_HANDSET_PRIMARY_HINT.search(primary_title_for_filter))

            # Pass 1: fetch all candidates, run quality filters (services / off-category / accessories),
            # but defer persistence so we can dedupe variants and apply price-band ranking with
            # complete information across the whole candidate set.
            staged: list[tuple[str, "NormalizedListing"]] = []
            relaxed_backfill: list[tuple[str, "NormalizedListing"]] = []
            primary_asin = target_asins[0] if target_asins else ""
            primary_thin_warned = False
            for idx, asin in enumerate(target_asins, start=1):
                # Reuse the primary listing we already fetched for spec inference; saves one ScraperAPI call.
                if asin == primary_asin and primary_nl_cache is not None:
                    nl = primary_nl_cache
                else:
                    try:
                        nl = await provider.fetch_listing(asin, amazon_domain)
                    except Exception as exc:
                        logger.warning("fetch_listing failed for %s (%s): %s", asin, amazon_domain, exc)
                        if asin == primary_asin:
                            raise
                        continue

                if job.flow == JobFlow.competitive and asin != primary_asin:
                    if _is_thin_listing(nl):
                        # ScraperAPI returned a thin/empty page even after render-retry.
                        # Persisting it would yield "Amazon product B0XXX" with all-N/A
                        # rows; skip instead.
                        job.phase = f"Skipped thin/blocked listing {asin}"
                        persist_job_touch(session, job)
                        continue
                    if _is_universal_service_listing(nl.title or "", nl.bsr_category, nl.product_category):
                        job.phase = f"Skipped service/plan listing {asin}"
                        persist_job_touch(session, job)
                        continue
                    if not _categories_compatible(
                        primary_bsr_category,
                        primary_product_category,
                        nl.bsr_category,
                        nl.product_category,
                    ):
                        # Keep as a low-priority fallback so we can still populate a useful
                        # comparison set when strict category gates starve the run.
                        relaxed_backfill.append((asin, nl))
                        job.phase = f"Deferred off-category listing {asin}"
                        persist_job_touch(session, job)
                        continue
                    if primary_is_handset and _is_service_or_accessory_listing(
                        nl.title or "", nl.bsr_category, nl.product_category
                    ):
                        relaxed_backfill.append((asin, nl))
                        job.phase = f"Deferred accessory listing {asin}"
                        persist_job_touch(session, job)
                        continue
                    if comparison_spec is not None and not comparison_spec.title_matches(nl.title or ""):
                        # Claude-derived must_match / must_not_match veto: e.g. "iPhone 17 Pro"
                        # tile bleeding into an "iPhone 17" comparison set.
                        relaxed_backfill.append((asin, nl))
                        job.phase = f"Deferred off-spec listing {asin}"
                        persist_job_touch(session, job)
                        continue

                if asin == primary_asin and _is_thin_listing(nl):
                    # Keep the primary (downstream needs *something*) but warn the user that
                    # the upstream scrape was empty so the report will be sparse.
                    if not primary_thin_warned:
                        job.error_message = (
                            "Primary product page came back empty/blocked from ScraperAPI; "
                            "downstream price, BSR, and revenue fields may be missing. "
                            "Try SCRAPERAPI_RENDER=true or retry the job."
                        )
                        primary_thin_warned = True

                staged.append((asin, nl))
                job.phase = f"Fetched listing ({idx}/{total or 1}): {asin}"
                persist_job_touch(session, job)

            # If strict gates produced too few peers, backfill from deferred candidates so
            # the UI can still compare near-target count (up to nine competitors).
            if job.flow == JobFlow.competitive and staged:
                desired_total = min(10, max(1, len(target_asins)))
                present = {a.upper() for a, _ in staged}
                for asin, nl in relaxed_backfill:
                    ua = asin.upper()
                    if ua in present:
                        continue
                    staged.append((asin, nl))
                    present.add(ua)
                    job.phase = f"Backfilled deferred listing {asin}"
                    persist_job_touch(session, job)
                    if len(staged) >= desired_total:
                        break

            # Pass 2 (competitive only): collapse variant SKUs by title fingerprint, drop extreme
            # price outliers, then rank by price proximity to the primary. Primary stays at index 0.
            if job.flow == JobFlow.competitive and staged:
                primary_pair = staged[0]
                primary_inr = convert_to_inr(primary_pair[1].price, primary_pair[1].currency, fx_rate)

                # Group competitors by fingerprint, keeping the best representative per group.
                groups: dict[str, list[tuple[str, "NormalizedListing"]]] = defaultdict(list)
                for asin, nl in staged[1:]:
                    fp = _title_fingerprint(nl.title) or asin.lower()
                    groups[fp].append((asin, nl))

                primary_fp = _title_fingerprint(primary_pair[1].title)

                def _rep_score(item: tuple[str, "NormalizedListing"]) -> tuple[int, float]:
                    nl = item[1]
                    return (
                        -(nl.review_count or 0),
                        nl.price if nl.price is not None else float("inf"),
                    )

                deduped: list[tuple[str, "NormalizedListing"]] = []
                for fp, members in groups.items():
                    # If a competitor group shares the primary's fingerprint, drop the whole group.
                    if primary_fp and fp == primary_fp:
                        for asin, _nl in members:
                            job.phase = f"Collapsed variant of primary {asin}"
                            persist_job_touch(session, job)
                        continue
                    members_sorted = sorted(members, key=_rep_score)
                    winner = members_sorted[0]
                    for asin, _nl in members_sorted[1:]:
                        job.phase = f"Collapsed variant {asin} (group: {fp[:48]})"
                        persist_job_touch(session, job)
                    deduped.append(winner)

                # Drop hard outliers (<=0.33x or >=3x of primary INR price). Skip when either side missing.
                price_filtered: list[tuple[str, "NormalizedListing"]] = []
                for asin, nl in deduped:
                    comp_inr = convert_to_inr(nl.price, nl.currency, fx_rate)
                    if primary_inr and comp_inr:
                        ratio = comp_inr / primary_inr
                        if ratio <= 0.33 or ratio >= 3.0:
                            job.phase = f"Skipped off-band price outlier {asin} (ratio {ratio:.2f})"
                            persist_job_touch(session, job)
                            continue
                    price_filtered.append((asin, nl))

                # Rank by closeness to primary price (log-distance handles asymmetry well).
                def _price_distance(item: tuple[str, "NormalizedListing"]) -> float:
                    if not primary_inr:
                        return 0.0
                    comp_inr = convert_to_inr(item[1].price, item[1].currency, fx_rate)
                    if not comp_inr:
                        return float("inf")
                    return abs(math.log(comp_inr / primary_inr))

                price_filtered.sort(key=_price_distance)
                # Cap at ten ASINs (primary + nine competitors). Pool may be larger so filters can refill peers.
                slice_cap = min(10, max(1, len(target_asins)))
                staged = [primary_pair, *price_filtered][:slice_cap]

            # Pass 3: persist Listing rows for the survivors and clean up any orphaned rows
            # (e.g. from a previous re-run that had different competitors).
            kept_asins = [a for a, _ in staged]
            kept_set = {a.upper() for a in kept_asins}

            for asin, nl in staged:
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

            # Drop any pre-existing Listing rows that are no longer in the survivor set.
            existing_rows = session.exec(select(Listing).where(Listing.job_id == job.id)).all()
            for row in existing_rows:
                if (row.asin or "").upper() not in kept_set:
                    session.delete(row)

            if job.flow == JobFlow.competitive and kept_asins != target_asins:
                target_asins = kept_asins
                job.asins = kept_asins
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
                total = len(target_asins)

                job.phase = "Review ingestion"
                persist_job_touch(session, job)

                for asin_idx, asin in enumerate(target_asins, start=1):
                    job.phase = f"Harvesting reviews ({asin_idx}/{total or 1}): {asin}"
                    persist_job_touch(session, job)
                    await ingest_reviews(provider, amazon_domain, job.id, asin, session, job.flow)

                job.phase = "LLM aggregation"
                persist_job_touch(session, job)

                # Space Claude calls so free-tier per-minute quotas are less likely to trip when
                # summarizing many ASINs back-to-back (ResourceExhausted).
                _GEMINI_INTER_ASIN_DELAY_S = 4.5

                for sidx, asin in enumerate(target_asins, start=1):
                    job.phase = f"Summarizing reviews ({sidx}/{total or 1})"
                    persist_job_touch(session, job)
                    if sidx > 1 and settings.anthropic_api_key.strip():
                        await asyncio.sleep(_GEMINI_INTER_ASIN_DELAY_S)
                    await summarize_asin(job, asin, session)

                if settings.youtube_data_api_key.strip():
                    job.phase = "YouTube signals"
                    persist_job_touch(session, job)
                    try:
                        primary_u = (target_asins[0] if target_asins else "").upper()
                        pr_title = primary_title_for_filter
                        for row in refreshed:
                            if (row.asin or "").upper() == primary_u and (row.title or "").strip():
                                pr_title = (row.title or "").strip()
                                break
                        yt_blob = await enrich_competitive_job_youtube_insights(
                            product_url=(job.product_url or "").strip(),
                            primary_asin=target_asins[0] if target_asins else "",
                            primary_title=pr_title,
                            primary_category=primary_bsr_category or primary_product_category,
                            listings=list(refreshed),
                        )
                        if yt_blob is not None:
                            job.youtube_insights = yt_blob
                            session.add(job)
                            session.commit()
                    except Exception as exc:
                        logger.warning("YouTube insights enrichment failed: %s", exc, exc_info=True)

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