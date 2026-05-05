# Pixii Market Intel

Pixii Market Intel is an Amazon product intelligence platform for:

- estimating market potential from Best Sellers pages,
- running competitive diagnostics against up to nine peer products,
- extracting buyer signals from synced reviews,
- and generating actionable `Key Purchase Criteria` using Claude.

It is built as a full-stack app with a FastAPI backend and a Next.js frontend.

## What the project does

Pixii has two primary workflows:

- **Market Size Snapshot**
  - Input: Amazon Best Sellers category URL.
  - Output: top listing leaderboard, BSR-driven velocity approximation, and INR monthly revenue rollup.

- **Competitive Diagnostics**
  - Input: a primary Amazon product URL/ASIN + optional competitors (or auto-discovery).
  - Output: side-by-side listing intelligence, synced review dossiers, `Key Purchase Criteria`, caution themes, and YouTube demand appendix.

## How it works (high level)

1. A job is created from the frontend (`market` or `competitive` flow).
2. Backend orchestrator resolves ASINs and storefront domain.
3. Product/listing data is fetched from Amazon via ScraperAPI.
4. Review pages are fetched, normalized, and stored.
5. Claude synthesizes review narratives and purchase criteria.
6. Optional YouTube enrichment is added for competitive runs.
7. Results are persisted and streamed back through polling on the job details page.

## APIs and services used

- **ScraperAPI**
  - Amazon PDP/Best Sellers/reviews scraping.
  - Used for listing metadata, BSR signals, and review capture.
  - Includes retry and fallback-key support.

- **Anthropic Claude API** (`claude-haiku-4-5-20251001`)
  - Review synthesis and criteria extraction (`Key Purchase Criteria`).
  - Comparison-spec inference for tighter competitor matching.
  - YouTube text consolidation and scoring summaries.

- **YouTube Data API v3**
  - Video search + metadata + comments sampling for demand/coverage/trend signals.
  - Used in competitive diagnostics appendix.

## Deployment

- **Backend:** deployed on **Render** (FastAPI service).
- **Frontend:** deployed on **Vercel** (Next.js app).

To reduce Render cold starts, a scheduled **cron job** periodically hits the backend health-check endpoint (keep-warm ping).  
This keeps the Render service active and improves first-response latency for users.

## Architecture at a glance

- **Frontend:** Next.js App Router + TypeScript + Tailwind.
- **Backend:** FastAPI + SQLModel + async job orchestration.
- **Database:** PostgreSQL.
- **Data model:** Jobs, Listings, Reviews, Summaries (+ YouTube insights blob on jobs).

## Reliability principles in this project

- Graceful degradation when third-party APIs are rate-limited or unavailable.
- Fallback API keys for critical providers (ScraperAPI, YouTube).
- Structured parsing + deterministic fallbacks for LLM output.
- Competitive flow designed to continue with partial data rather than hard-fail.

## Outcome

Pixii turns raw marketplace and review data into a practical decision layer for product teams:  
**what to compare, what buyers care about, where demand is visible, and how to prioritize listing improvements.**
