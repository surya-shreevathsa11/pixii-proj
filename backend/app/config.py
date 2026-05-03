from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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

    max_reviews_per_asin: int = 1000
    review_batch_map_size: int = 100


    keepa_api_key: str = ""


settings = Settings()
