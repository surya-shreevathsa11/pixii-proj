# pixii-proj

## Run locally (quick start)

Do this from the **repo root** (`pixii-proj/`) unless a step says `cd backend` or `cd frontend`.

> **TL;DR — three terminals, copy-paste:**
>
> ```bash
> # T1: database
> docker compose up -d
>
> # T2: backend (FastAPI on :8000)
> cd backend && python3 -m venv .venv && source .venv/bin/activate \
>   && pip install -U pip && pip install -U -r requirements.txt \
>   && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
>
> # T3: frontend (Next.js on :3000)
> cd frontend && npm install && npm run dev
> ```
>
> If you came back to the project later, the **only commands you need** are
> `docker compose up -d`, `source backend/.venv/bin/activate && pip install -U -r backend/requirements.txt`,
> then start uvicorn and `npm run dev` again.

### Prerequisites

| Tool | Notes |
|------|--------|
| **Docker** | For PostgreSQL only (`docker compose up -d`). |
| **Python** | **3.12, 3.13, or 3.14** all work. Use `python3.12 -m venv .venv` (or `python3.14`) if `python3` points at an older interpreter. |
| **Node.js** | **18+** for the Next.js app. |

Pinned versions in `backend/requirements.txt` that matter for local dev:

- **`psycopg2-binary==2.9.12`** — wheels published for **Python 3.14**, no `pg_config` / libpq dev packages required.
- **`sqlmodel==0.0.38`** — compatible with **Pydantic ≥ 2.11**. Older `0.0.22` triggers `PydanticUserError: Field 'id' requires a type annotation` on Python 3.14.

### 1) Database

```bash
docker compose up -d
```

Defaults: user `pixii`, password `pixii`, database `amazon_analytics`, port **5432** (see `docker-compose.yml`).

### 2) Backend API

First time:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -U -r requirements.txt
```

Coming back later (existing venv): just **upgrade** in case any pin changed:

```bash
cd backend
source .venv/bin/activate
pip install -U -r requirements.txt
```

**`backend/.env`** (create this file if you do not have one). For a first run you only need Postgres to match Docker; scraping defaults to **mock** data without API keys:

```env
DATABASE_URL=postgresql://pixii:pixii@localhost:5432/amazon_analytics
# Optional: explicit mock mode (same as app default if omitted)
SCRAPING_PROVIDER=mock
```

To use real scraping later, add to `backend/.env`:

```env
SCRAPING_PROVIDER=scraperapi
SCRAPING_API_KEY=your_scraperapi_key
# Leave SCRAPERAPI_COUNTRY_CODE empty for multi-region; uncomment to lock proxy geo:
# SCRAPERAPI_COUNTRY_CODE=us
# Optional Gemini for review summaries:
# GOOGLE_API_KEY=your_gemini_key
# Optional Apify for 90-day price history on competitive jobs (both keys required):
# APIFY_API_TOKEN=your_apify_token
# APIFY_PRICE_HISTORY_ACTOR=user~actor-name
```

Start **uvicorn from `backend/`** so `backend/.env` is loaded reliably:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Health: [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)  
- If `uvicorn` is not on PATH: `./.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

### 3) Frontend

New terminal:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

The UI calls **`/api/...` on the Next dev server**; `frontend/next.config.mjs` **rewrites** those to FastAPI at **`http://127.0.0.1:8000`**, so you usually **do not** set `NEXT_PUBLIC_API_BASE` locally (avoids CORS and `localhost` vs `127.0.0.1` mismatches).

If the API is not on port **8000**, create **`frontend/.env.local`** and restart `npm run dev`:

```env
API_PROXY_TARGET=http://127.0.0.1:8000
```

Use **`NEXT_PUBLIC_API_BASE`** only when the UI and API are on different deployed hosts **and** CORS is configured on the backend (`CORS_ORIGINS`).

### 4) Checklist

1. `docker compose up -d`  
2. Backend venv + `pip install -U -r requirements.txt` + **uvicorn on 8000** (cwd = `backend/`)  
3. Frontend `npm install` + **`npm run dev`** on **3000**  

Stop Postgres when finished: `docker compose down` (add `-v` to delete the data volume).
Stop the backend / frontend with **Ctrl+C** in their respective terminals.

### Troubleshooting (local)

| Issue | What to try |
|--------|--------------|
| **`pg_config` / build errors for `psycopg2-binary`** | Use the repo’s current `requirements.txt` (wheels for 3.14). Prefer `pip install -U pip` then reinstall. Optional: `pip install --only-binary=:all: -r requirements.txt`. |
| **Frontend cannot reach API** | Confirm uvicorn is listening on **8000**, `API_PROXY_TARGET` matches, restart **`npm run dev`** after changing `.env.local`. |
| **`ModuleNotFoundError` / wrong Python** | Recreate the venv with the intended binary: `rm -rf backend/.venv && cd backend && python3.12 -m venv .venv` (or `python3.14`). |
| **`PydanticUserError: Field 'id' requires a type annotation`** | An older venv has `sqlmodel==0.0.22` (incompatible with newer Pydantic). Upgrade: `cd backend && source .venv/bin/activate && pip install -U -r requirements.txt`. |

---

## Advanced: scraping, reviews, and `.env`

On startup, `database.py` applies idempotent patches for newer columns (e.g. `listing.product_category`, `review.has_customer_images`, `summary.why_buyers_like`, `summary.why_buyers_caution`). For manual SQL on older Postgres installs:

```sql
ALTER TABLE listing ADD COLUMN IF NOT EXISTS product_category VARCHAR(512);
ALTER TABLE review ADD COLUMN IF NOT EXISTS has_customer_images BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE summary ADD COLUMN IF NOT EXISTS why_buyers_like TEXT;
ALTER TABLE summary ADD COLUMN IF NOT EXISTS why_buyers_caution TEXT;
```

If you already have a `job` table from an older install, add the auto-discover column once (skip if the column already exists):

```sql
ALTER TABLE job ADD COLUMN auto_discover_competitors BOOLEAN NOT NULL DEFAULT false;
```

SQLite example: `ALTER TABLE job ADD COLUMN auto_discover_competitors BOOLEAN DEFAULT 0;`

If you also have a `listing` table from an older install, add the revenue/INR columns:

```sql
ALTER TABLE listing ADD COLUMN previous_month_units INTEGER;
ALTER TABLE listing ADD COLUMN revenue_basis VARCHAR(32) NOT NULL DEFAULT 'unknown';
ALTER TABLE listing ADD COLUMN unit_price_inr DOUBLE PRECISION;
```

SQLite variant:

```sql
ALTER TABLE listing ADD COLUMN previous_month_units INTEGER;
ALTER TABLE listing ADD COLUMN revenue_basis TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE listing ADD COLUMN unit_price_inr REAL;
```

### Review ingest behaviour

- **Market jobs**: when `REVIEWS_ONLY_WITH_CUSTOMER_IMAGES=true` (default), only reviews flagged with customer photos are persisted, up to `MAX_REVIEWS_PER_ASIN` (default 400). Set `REVIEWS_ONLY_WITH_CUSTOMER_IMAGES=false` to keep all text reviews.
- **Competitive jobs**: up to `COMPETITIVE_REVIEWS_PER_ASIN` (default 10) recent reviews per ASIN; rows with customer images are ranked ahead of text-only rows, but text reviews still fill the cap. `COMPETITIVE_REVIEW_FETCH_BUFFER` (default 40) controls how many recent rows are fetched before sorting and trimming.
- **amazon.in + ScraperAPI**: review pages are often JS-heavy. Set `SCRAPERAPI_RENDER=true` in `backend/.env` for the most reliable results; the scraper also retries with `render=true` on `.in` when the first HTML pass returns no review blocks, and can retry the structured reviews endpoint with render. Rendered calls can exceed 120s, so defaults use `SCRAPERAPI_RENDER_TIMEOUT_SECONDS=300` (override in `.env` if needed) and `SCRAPERAPI_TIMEOUT_SECONDS=120` for non-render fetches; transient read timeouts retry up to three times. To debug one ASIN from a shell: `cd backend && source .venv/bin/activate && python scripts/fetch_reviews_debug.py B0YOURASIN amazon.in`
- **Multi-region Amazon + ScraperAPI**: paste a full product or bestsellers URL for the storefront you want (`amazon.com`, `amazon.co.uk`, `amazon.de`, `amazon.in`, etc.). The job runner resolves the host from that URL (fallback: `AMAZON_DOMAIN` in `.env`). Use **one** ScraperAPI key for all regions; leave **`SCRAPERAPI_COUNTRY_CODE` empty** so proxy geo follows the resolved storefront (setting a fixed country forces that region for every job). There is no separate `amazon.eu` retail host in the resolver; use the country site (e.g. `amazon.de`).
- **Currency**: each `listing` row stores **Amazon listing price + ISO currency** from the PDP. Estimated **monthly revenue** fields stay **normalized to INR** for a single rollup column; the analysis UI shows storefront prices in the listing currency and labels INR revenue explicitly.

Revenue is always stored and displayed in INR. To override the static USD→INR fallback when both live FX endpoints fail, set `USD_TO_INR_RATE=83` (or similar) in `backend/.env`.

### Apify 90-day price history (competitive jobs only)

Optional. When both `APIFY_API_TOKEN` and `APIFY_PRICE_HISTORY_ACTOR` are set, every competitive job
makes **one** Apify run-sync call for the **primary ASIN only** and stores the resulting series in
the `price_history` table. The job page then renders an inline SVG line chart above the leaderboard,
and any historical analysis you revisit from "Recent analyses" automatically shows the same chart
because it's served from the database, not re-fetched.

```env
# Both required to enable. Leave either empty to disable the feature without errors.
APIFY_API_TOKEN=          # https://console.apify.com/account/integrations
APIFY_PRICE_HISTORY_ACTOR=  # e.g. user~amazon-price-history (slug from your Apify Console actor page)
# APIFY_TIMEOUT_SECONDS=90
# PRICE_HISTORY_DAYS=90
```

Operational notes:

- **Failure is non-fatal**: an Apify error or timeout logs a warning and the rest of the job (reviews, summaries) still completes.
- **Cost**: one actor run per competitive job. Subsequent visits to the same job page read from Postgres only.
- **Actor compatibility**: the parser handles common shapes (`{date, price, currency}`, `{d, p}`, `{priceHistory: [...]}`, `{prices: [...]}`, etc.). If your chosen actor returns something exotic, only `backend/app/services/price_history.py::parse_apify_payload` needs adjusting.
- **Market jobs** are unaffected — price history is competitive-only by design.
