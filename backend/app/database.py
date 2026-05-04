import logging

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

logger = logging.getLogger(__name__)

_url = settings.database_url
_sqlite = _url.startswith("sqlite")

_engine_kwargs: dict = {}
if _sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    _engine_kwargs["pool_pre_ping"] = False
else:
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(_url, **_engine_kwargs)


def _table_exists(bind, table: str) -> bool:
    return bool(inspect(bind).has_table(table))


def _column_names(bind, table: str) -> set[str]:
    try:
        return {c["name"].lower() for c in inspect(bind).get_columns(table)}
    except Exception:
        return set()


def _apply_schema_patches() -> None:
    """Add columns introduced after the first deploy.

    ``create_all`` never alters existing tables, so older Postgres/SQLite files miss
    new fields and INSERTs 500. Patches are idempotent.
    """
    with engine.begin() as conn:
        if not _table_exists(conn, "job"):
            return

        cols_job = _column_names(conn, "job")
        dialect = conn.dialect.name

        if "auto_discover_competitors" not in cols_job:
            if dialect == "postgresql":
                conn.execute(
                    text(
                        "ALTER TABLE job ADD COLUMN IF NOT EXISTS auto_discover_competitors "
                        "BOOLEAN NOT NULL DEFAULT false"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE job ADD COLUMN auto_discover_competitors INTEGER NOT NULL DEFAULT 0"
                    )
                )
            logger.info("Applied schema patch: job.auto_discover_competitors")

        if not _table_exists(conn, "listing"):
            return

        cols_listing = _column_names(conn, "listing")

        if "previous_month_units" not in cols_listing:
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE listing ADD COLUMN IF NOT EXISTS previous_month_units INTEGER"))
            else:
                conn.execute(text("ALTER TABLE listing ADD COLUMN previous_month_units INTEGER"))
            logger.info("Applied schema patch: listing.previous_month_units")

        if "revenue_basis" not in cols_listing:
            if dialect == "postgresql":
                conn.execute(
                    text(
                        "ALTER TABLE listing ADD COLUMN IF NOT EXISTS revenue_basis VARCHAR(32) "
                        "NOT NULL DEFAULT 'unknown'"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE listing ADD COLUMN revenue_basis TEXT NOT NULL DEFAULT 'unknown'"
                    )
                )
            logger.info("Applied schema patch: listing.revenue_basis")

        if "unit_price_inr" not in cols_listing:
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE listing ADD COLUMN IF NOT EXISTS unit_price_inr DOUBLE PRECISION"))
            else:
                conn.execute(text("ALTER TABLE listing ADD COLUMN unit_price_inr REAL"))
            logger.info("Applied schema patch: listing.unit_price_inr")

        if "product_category" not in cols_listing:
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE listing ADD COLUMN IF NOT EXISTS product_category VARCHAR(512)"))
            else:
                conn.execute(text("ALTER TABLE listing ADD COLUMN product_category TEXT"))
            logger.info("Applied schema patch: listing.product_category")

        if _table_exists(conn, "review"):
            cols_review = _column_names(conn, "review")
            if "has_customer_images" not in cols_review:
                if dialect == "postgresql":
                    conn.execute(
                        text(
                            "ALTER TABLE review ADD COLUMN IF NOT EXISTS has_customer_images "
                            "BOOLEAN NOT NULL DEFAULT false"
                        )
                    )
                else:
                    conn.execute(
                        text(
                            "ALTER TABLE review ADD COLUMN has_customer_images INTEGER NOT NULL DEFAULT 0"
                        )
                    )
                logger.info("Applied schema patch: review.has_customer_images")

        if _table_exists(conn, "summary"):
            cols_summary = _column_names(conn, "summary")
            if "why_buyers_like" not in cols_summary:
                if dialect == "postgresql":
                    conn.execute(text("ALTER TABLE summary ADD COLUMN IF NOT EXISTS why_buyers_like TEXT"))
                else:
                    conn.execute(text("ALTER TABLE summary ADD COLUMN why_buyers_like TEXT"))
                logger.info("Applied schema patch: summary.why_buyers_like")
            if "why_buyers_caution" not in cols_summary:
                if dialect == "postgresql":
                    conn.execute(text("ALTER TABLE summary ADD COLUMN IF NOT EXISTS why_buyers_caution TEXT"))
                else:
                    conn.execute(text("ALTER TABLE summary ADD COLUMN why_buyers_caution TEXT"))
                logger.info("Applied schema patch: summary.why_buyers_caution")

        # New table for Apify-backed price history (older DBs never ran create_all after this model existed).
        if not _table_exists(conn, "price_history"):
            if dialect == "postgresql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE price_history (
                            id UUID NOT NULL PRIMARY KEY,
                            job_id UUID NOT NULL,
                            asin VARCHAR(32) NOT NULL,
                            currency VARCHAR(8) NOT NULL DEFAULT '',
                            points JSON NOT NULL,
                            source VARCHAR(128) NOT NULL DEFAULT '',
                            captured_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                            CONSTRAINT fk_price_history_job_id FOREIGN KEY (job_id) REFERENCES job(id)
                        )
                        """
                    )
                )
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_history_job_id ON price_history (job_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_history_asin ON price_history (asin)"))
            else:
                conn.execute(
                    text(
                        """
                        CREATE TABLE price_history (
                            id VARCHAR(36) NOT NULL PRIMARY KEY,
                            job_id VARCHAR(36) NOT NULL,
                            asin VARCHAR(32) NOT NULL,
                            currency VARCHAR(8) NOT NULL DEFAULT '',
                            points TEXT NOT NULL,
                            source VARCHAR(128) NOT NULL DEFAULT '',
                            captured_at TIMESTAMP NOT NULL,
                            FOREIGN KEY(job_id) REFERENCES job(id)
                        )
                        """
                    )
                )
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_history_job_id ON price_history (job_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_history_asin ON price_history (asin)"))
            logger.info("Applied schema patch: price_history table")


def init_db() -> None:
    # Ensure every SQLModel table is registered on metadata before create_all (import-order safe).
    import app.models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _apply_schema_patches()


def get_session():
    with Session(engine) as session:
        yield session
