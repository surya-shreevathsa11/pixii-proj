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

Optional (competitive jobs): by default the API keeps only reviews that include **customer-uploaded photos**, up to `MAX_REVIEWS_PER_ASIN` (default 400). To ingest all text reviews again, set `REVIEWS_ONLY_WITH_CUSTOMER_IMAGES=false`.

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

By default the UI calls the API at `http://127.0.0.1:8000`. To override, set in `frontend/.env.local`:

```env
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

### 4. Typical order

1. `docker compose up -d`
2. Backend on port **8000**
3. Frontend on port **3000**

Stop Postgres when finished: `docker compose down` (add `-v` to remove the data volume).
