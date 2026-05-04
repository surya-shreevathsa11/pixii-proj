from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, init_db
from app.api.jobs import router as jobs_router
from app.schemas import BootstrapResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Pixii Market Intel", lifespan=lifespan)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if not origins:
    origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

# Any dev port on localhost / LAN — avoids "NetworkError" when Next runs on :3001 or you open via 192.168.x.x.
_dev_origin_regex = (
    r"https?://("
    r"localhost|127\.0\.0\.1|\[::1\]|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r")(?::\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=_dev_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs_router, prefix="/api")


@app.get("/health")
def health_root():
    """Liveness probe for load balancers and hosting platforms that expect ``/health``."""
    return {"status": "ok"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/bootstrap", response_model=BootstrapResponse)
def bootstrap():
    return BootstrapResponse(
        scraping_provider=settings.scraping_provider,
        gemini_configured=bool(settings.google_api_key.strip()),
    )
