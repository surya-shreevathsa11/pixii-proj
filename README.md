# pixii-proj

## Run locally (quick start)

Do this from the **repo root** (`pixii-proj/`) unless a step says `cd backend` or `cd frontend`.

### Prerequisites

| Tool | Notes |
|------|--------|
| **Docker** | For PostgreSQL only (`docker compose up -d`). |
| **Python** | **3.12, 3.13, or 3.14** recommended. Use `python3.12 -m venv .venv` (or `python3.14`) if `python3` points at an older interpreter. |
| **Node.js** | **18+** for the Next.js app. |

`backend/requirements.txt` pins **`psycopg2-binary==2.9.12`**, which has **pre-built wheels for Python 3.14** (no `pg_config` / libpq dev packages needed for a normal `pip install`).

### 1) Database

```bash
docker compose up -d
```

Defaults: user `pixii`, password `pixii`, database `amazon_analytics`, port **5432** (see `docker-compose.yml`).

### 2) Backend API

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

**`backend/.env`** (create this file if you do not have one). For a first run you only need Postgres to match Docker; scraping defaults to **mock** data without API keys:

```env
DATABASE_URL=postgresql://pixii:pixii@localhost:5432/amazon_analytics
# Optional: explicit mock mode (same as app default if omitted)
SCRAPING_PROVIDER=mock
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
2. Backend venv + `pip install -r requirements.txt` + **uvicorn on 8000** (cwd = `backend/`)  
3. Frontend `npm install` + **`npm run dev`** on **3000**  

Stop Postgres: `docker compose down` (add `-v` to delete the data volume).

### Troubleshooting (local)

| Issue | What to try |
|--------|--------------|
| **`pg_config` / build errors for `psycopg2-binary`** | Use the repo’s current `requirements.txt` (wheels for 3.14). Prefer `pip install -U pip` then reinstall. Optional: `pip install --only-binary=:all: -r requirements.txt`. |
| **Frontend cannot reach API** | Confirm uvicorn is listening on **8000**, `API_PROXY_TARGET` matches, restart **`npm run dev`** after changing `.env.local`. |
| **`ModuleNotFoundError` / wrong Python** | Recreate the venv with the intended binary: `rm -rf backend/.venv && cd backend && python3.12 -m venv .venv` (or `python3.14`). |

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
