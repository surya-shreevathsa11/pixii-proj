from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ENV_FILE = _BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    # Always load backend/.env (not cwd-relative), so one file is canonical regardless of where uvicorn is started.
    model_config = SettingsConfigDict(
        env_file=str(_DEFAULT_ENV_FILE) if _DEFAULT_ENV_FILE.is_file() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://pixii:pixii@localhost:5432/amazon_analytics"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    scraping_provider: str = "mock"
    scraping_api_key: str = ""
    scraperapi_render: bool = False
    scraperapi_country_code: str = ""
    scraperapi_save_html_on_empty: bool = False
    amazon_domain: str = "amazon.com"

    google_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Cap on reviews persisted per ASIN (competitive flow). When reviews_only_with_customer_images is True,
    # only reviews that include customer-uploaded photos count toward this cap.
    max_reviews_per_asin: int = 400
    review_batch_map_size: int = 100
    reviews_only_with_customer_images: bool = True


    keepa_api_key: str = ""

    # Revenue display normalization. Backend always reports estimated revenue in INR;
    # the env rate is used as a static fallback when live FX endpoints are unreachable.
    display_currency: str = "INR"
    usd_to_inr_rate: float = 83.0
    fx_cache_ttl_seconds: int = 21600


settings = Settings()
