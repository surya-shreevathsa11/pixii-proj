# pixii-proj

## Run locally

### Prerequisites

- Docker (for PostgreSQL)
- Python 3.12+ (project uses a `backend/.venv`)
- Node.js 18+ (for the Next.js frontend)

### 1. Start PostgreSQL

From the repository root:

```bash
docker compose up -d
```

Defaults: user `pixii`, password `pixii`, database `amazon_analytics`, port `5432`.

### 2. Backend (FastAPI)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create `backend/.env` if you do not have one yet. At minimum set the database URL to match Docker:

```env
DATABASE_URL=postgresql://pixii:pixii@localhost:5432/amazon_analytics
```

Optional review ingest:

- **Market jobs**: when `REVIEWS_ONLY_WITH_CUSTOMER_IMAGES=true` (default), only reviews flagged with customer photos are persisted, up to `MAX_REVIEWS_PER_ASIN` (default 400). Set `REVIEWS_ONLY_WITH_CUSTOMER_IMAGES=false` to keep all text reviews.
- **Competitive jobs**: up to `COMPETITIVE_REVIEWS_PER_ASIN` (default 10) recent reviews per ASIN; rows with customer images are ranked ahead of text-only rows, but text reviews still fill the cap. `COMPETITIVE_REVIEW_FETCH_BUFFER` (default 40) controls how many recent rows are fetched before sorting and trimming.

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

Revenue is always stored and displayed in INR. To override the static USD->INR fallback when both live FX endpoints fail, set `USD_TO_INR_RATE=83` (or similar) in `backend/.env`.

Run the API from the **`backend`** directory (so `.env` is picked up):

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

If `uvicorn` is not found, use the venv binary: `./.venv/bin/uvicorn …` or ensure the venv is activated.

Health check: [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)

### 3. Frontend (Next.js)

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

By default the browser talks to the **same origin** only (`/api/...` on the Next dev server). Next.js **rewrites** those requests to FastAPI at `http://127.0.0.1:8000`, which avoids CORS and fixes `NetworkError when attempting to fetch resource` when the UI is opened as `localhost` while the old client pointed at `127.0.0.1`, or when using another port / LAN IP.

If FastAPI is not on port 8000, set in `frontend/.env.local` and restart `npm run dev`:

```env
API_PROXY_TARGET=http://127.0.0.1:8000
```

Only set `NEXT_PUBLIC_API_BASE` when the UI and API are on different hosts in production and you have CORS configured—leave it unset for local dev so the proxy is used.

### 4. Typical order

1. `docker compose up -d`
2. Backend on port **8000**
3. Frontend on port **3000**

Stop Postgres when finished: `docker compose down` (add `-v` to remove the data volume).
