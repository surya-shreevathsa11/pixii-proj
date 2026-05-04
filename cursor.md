# Pixii — project charter and technical primer

This document was written at project kickoff to align engineers and AI assistants on **what we are building**, **how the pieces fit**, and **how to work in the repo** without rediscovering context from scattered commits. Treat it as the canonical orientation before touching code or infrastructure.

---

## 1. Product intent

**Pixii** is an internal-facing Amazon analytics workspace. A user pastes either:

- A **bestsellers** URL (market flow), or  
- A **product** URL with optional competitor URLs or **auto-discover** (competitive flow).

The system **scrapes** listing metadata and customer reviews from the chosen Amazon storefront, **estimates** revenue signals (badge-based units where visible, BSR heuristics as fallback), **normalises** currency display while keeping a single **INR** rollup for comparison, and **summarises** review themes with **Google Gemini** where an API key is configured.

The goal is **actionable competitive diagnostics**: who is winning on estimated monthly revenue, what buyers praise or complain about, and crisp **key purchase criteria** derived from real review text—not a generic “AI wrapper” over static PDP copy alone.

---

## 2. Non-goals (explicit)

- We are **not** an official Amazon product; all numbers are **estimates** and must be labelled as such in the UI.  
- We do **not** guarantee scrape success on every ASIN; Amazon and proxy providers change behaviour frequently. The backend degrades gracefully (skip thin rows, continue jobs, surface warnings).  
- We are **not** optimising for every international marketplace edge case on day one; we support common hosts (`amazon.com`, `.co.uk`, `.de`, `.in`, etc.) with storefront-aware URL resolution and ScraperAPI country inference when `SCRAPERAPI_COUNTRY_CODE` is left empty.

---

## 3. High-level architecture

```
┌─────────────────┐     HTTPS      ┌──────────────────┐
│  Next.js (UI)   │ ◄────────────► │  FastAPI (API)   │
│  frontend/      │   JSON + CORS  │  backend/app/    │
└────────┬────────┘                └────────┬─────────┘
         │                                  │
         │                           ┌──────▼──────┐
         │                           │ PostgreSQL  │
         │                           │ (SQLModel)  │
         │                           └──────┬──────┘
         │                                  │
         │                    ┌─────────────▼─────────────┐
         │                    │ Background job runner     │
         │                    │ (orchestrate per job id) │
         │                    └─────────────┬─────────────┘
         │                                  │
         │              ┌─────────────────────┼─────────────────────┐
         │              ▼                     ▼                     ▼
         │      ScraperAPI provider    Google Gemini          FX / revenue
         │      (HTML + structured)   (review synthesis)     helpers
         └──────────────────────────────────────────────────────────────
```

- **Frontend**: Next.js App Router, client-heavy job detail pages, Tailwind for layout.  
- **Backend**: FastAPI, Pydantic v2 schemas for I/O, **SQLModel** for ORM and migrations via `create_all` plus **idempotent SQL patches** for older databases.  
- **Jobs**: Long-running work is triggered from API routes and executed in-process (e.g. FastAPI `BackgroundTasks`); the job row tracks `phase`, `status`, and optional `error_message` for UX polling.  
- **Secrets**: Never commit `backend/.env`; copy from root `.env.example` patterns and document keys in README for humans only.

---

## 4. Repository layout

| Path | Role |
|------|------|
| `backend/app/main.py` | FastAPI app, CORS, router mounting, `/health` and `/api/health`. |
| `backend/app/config.py` | `pydantic-settings` `Settings` — single source of truth for env-backed configuration. |
| `backend/app/models.py` | SQLModel tables: `Job`, `Listing`, `Review`, `Summary`, etc. |
| `backend/app/database.py` | Engine, `init_db()`, schema patches for additive columns/tables. |
| `backend/app/api/` | Route modules (`jobs.py` is the main surface). |
| `backend/app/schemas.py` | Pydantic request/response models shared with OpenAPI. |
| `backend/app/services/job_runner.py` | **Orchestration**: market vs competitive pipeline, listing ingest, review ingest, Gemini summaries. |
| `backend/app/services/scraping/` | `ScraperApiScrapingProvider`, `MockScrapingProvider`, `factory.get_scraping_provider()`. |
| `backend/app/services/llm_review.py` | Gemini: batched theme maps, single-pass competitive synthesis, reduce step. |
| `backend/app/services/comparison_spec.py` | Optional one-call Gemini **comparison spec** for smarter competitor discovery (SERP query + title filters). |
| `backend/app/services/revenue.py` | INR revenue computation, BSR-based unit estimates, FX helpers. |
| `frontend/app/` | Routes: landing, `market/`, `competitive/`, `jobs/[jobId]/`. |
| `frontend/lib/api.ts` | Typed fetch helpers against the backend. |
| `frontend/lib/types.ts` | Mirrors critical API response shapes. |
| `docker-compose.yml` | Local PostgreSQL for development. |
| `README.md` | Operator-focused runbook (kept current with env vars and pitfalls). |

---

## 5. Environment variables (backend)

All backend configuration flows through `Settings` in `backend/app/config.py`, loaded from **`backend/.env`** (path pinned in settings so cwd does not matter).

**Core**

- `DATABASE_URL` — PostgreSQL DSN (local Docker or hosted).  
- `CORS_ORIGINS` — Comma-separated browser origins allowed to call the API.  

**Scraping**

- `SCRAPING_PROVIDER` — `mock` (deterministic fixtures) or `scraperapi`.  
- `SCRAPING_API_KEY` — ScraperAPI key when provider is `scraperapi`.  
- `SCRAPERAPI_RENDER` — When `true`, requests use `render=true` (slower, higher credit use; often required for JS-heavy Amazon pages, especially reviews on `amazon.in`).  
- `SCRAPERAPI_COUNTRY_CODE` — Prefer **empty**: country is inferred per job from the product/bestsellers URL so multi-region links work.  
- `SCRAPERAPI_TIMEOUT_SECONDS`, `SCRAPERAPI_RENDER_TIMEOUT_SECONDS` — Read timeouts for plain vs rendered fetches.  
- `AMAZON_DOMAIN` — Fallback storefront when a job URL does not imply a host.  

**LLM**

- `GOOGLE_API_KEY` — Enables Gemini paths in `llm_review.py` and `comparison_spec.py`.  
- `GEMINI_MODEL` — Model id passed to the Google Generative AI client (with internal fallbacks in code if a name is deprecated).  

**Reviews and competitive caps**

- `MAX_REVIEWS_PER_ASIN`, `COMPETITIVE_REVIEWS_PER_ASIN`, `COMPETITIVE_REVIEW_FETCH_BUFFER`, `REVIEW_BATCH_MAP_SIZE`, `REVIEWS_ONLY_WITH_CUSTOMER_IMAGES` — Tuning knobs documented in README.  

**Revenue display**

- `DISPLAY_CURRENCY`, `USD_TO_INR_RATE`, `FX_CACHE_TTL_SECONDS` — Normalisation and static FX fallback.  

Optional keys like `KEEPA_API_KEY` exist in settings for future or experimental integrations; unused keys are ignored at runtime if not referenced.

---

## 6. Data model (conceptual)

- **Job** — One analysis run: `flow` (`market` | `competitive`), URLs, ASIN list, status machine (`queued` → `running` → `completed` | `failed`), `phase` string for live progress, `error_message` for user-visible failures or warnings.  
- **Listing** — One row per ASIN per job: title, price, currency, BSR, categories, review counts, canonical URL, estimated revenue fields, raw JSON metadata for debugging.  
- **Review** — Snippets persisted per ASIN (caps differ by flow); includes flags for verified purchase and customer images where parseable.  
- **Summary** — Per-ASIN Gemini output: long-form summary, key purchase criteria (list), optional “why buyers like / caution” fields, optional map batches for market-style multi-pass reduction.

Schema evolution: prefer **idempotent** `ALTER TABLE` / `CREATE TABLE` blocks in `database.py` so production databases created before a model existed still migrate cleanly on deploy.

---

## 7. Job flows

### Market (`JobFlow.market`)

1. Resolve ASINs from a bestsellers page via the scraping provider.  
2. Fetch listing metadata for each ASIN.  
3. Ingest reviews up to `MAX_REVIEWS_PER_ASIN` (with optional image-only filtering).  
4. Run Gemini batch mapping + reduction for summaries when configured.  
5. Attach a short **market totals note** for UI context.

### Competitive (`JobFlow.competitive`)

1. Resolve primary ASIN from product URL; optionally **auto-discover** related ASINs (carousel + SERP heuristics, cross-brand ranking, category and price filters). A **comparison spec** from Gemini may refine SERP query and post-fetch title filters when `GOOGLE_API_KEY` is set.  
2. **Prefetch primary listing** early so filters and discovery share canonical title/category.  
3. **Listing pass** — fetch each candidate PDP; drop **thin** scrapes (empty body / unparsed shells after render retry) for competitors; keep primary with a warning if thin.  
4. **Dedupe variants**, drop price outliers vs primary, cap to ten ASINs for persistence.  
5. Ingest capped competitive reviews per ASIN.  
6. **Single-pass Gemini synthesis** per ASIN when review count is small (typical for competitive caps), feeding rating-prefixed review lines into the prompt for **key purchase criteria**.

---

## 8. API conventions

- REST JSON under `/api/` (see `backend/app/main.py` for prefix).  
- Job creation returns a **job id** immediately; the client polls job detail until `status` is terminal.  
- Use Pydantic response models for stable OpenAPI and frontend typing.  
- Errors: validation → `400` with clear `detail`; unexpected server errors → `500` with traceback logged server-side.

---

## 9. Frontend conventions

- **Server vs client**: Job detail uses client fetch + polling patterns suitable for long phases.  
- **Money formatting**: storefront currency from listing rows; INR rollup labelled explicitly in copy.  
- **Accessibility**: semantic headings, `aria-label` on jump-nav where used.  
- **Types**: extend `frontend/lib/types.ts` whenever backend schemas gain fields—avoid `any` in new code.

---

## 10. Local development workflow

1. `docker compose up -d` for Postgres.  
2. `cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`  
3. Copy env: create `backend/.env` from README examples; set `DATABASE_URL` and optionally scraping + Gemini keys.  
4. `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` from **`backend/`** so `.env` resolves.  
5. `cd frontend && npm install && npm run dev` — ensure `CORS_ORIGINS` includes the exact origin (including port).  

**Tests** (backend): `cd backend && source .venv/bin/activate && python -m unittest discover -s tests -p 'test_*.py'`

---

## 11. Engineering principles

- **Prefer correctness over silent failure** for user-visible aggregates: log scrape anomalies, surface `job.error_message` when continuing with degraded data.  
- **Do not leak secrets** in logs, README examples, or client bundles.  
- **Match existing style** when editing: typing, logging, and small focused diffs.  
- **Multi-region**: resolve Amazon host from user URLs; avoid hard-coding a single marketplace in business logic.

---

## 12. How Cursor / AI assistants should use this file

- Read **`cursor.md` first**, then **`README.md`** for copy-paste commands and env tables.  
- When adding features, update **schemas**, **models**, **database patches** (if persisted), **job runner**, and **frontend types** together so the contract stays coherent.  
- When debugging scrape issues, start in **`scraperapi.py`** (`fetch_listing`, `fetch_reviews_page`, `discover_competitor_asins`) and enable **`SCRAPERAPI_SAVE_HTML_ON_EMPTY`** locally to capture HTML samples under `backend/var/debug_scraperapi/`.

---

## 13. Glossary

| Term | Meaning |
|------|---------|
| **ASIN** | Amazon Standard Identification Number (10-character product id). |
| **PDP** | Product detail page. |
| **BSR** | Best Sellers Rank; used heuristically when “bought in past month” is absent. |
| **KPC** | Key purchase criteria — bullet list from Gemini grounded in reviews. |
| **Thin listing** | Scrape succeeded HTTP-wise but HTML lacked parseable title/price/BSR; competitors are dropped; primary may be kept with a warning. |

---

*Last aligned with repository layout and behaviour at kickoff; when behaviour drifts, update this file in the same change as the code.*
