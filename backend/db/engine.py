from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import config


def _build_database_url() -> str:
    # Standalone default: local SQLite file. Set APP_DB_URL to a Postgres URL to
    # use Postgres instead (e.g. postgresql+psycopg2://user:pass@host:5432/db).
    url = os.getenv("APP_DB_URL", "").strip()
    if url:
        return url
    return f"sqlite:///{config.APP_DB_PATH}"


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def is_app_db_enabled() -> bool:
    # Always available in standalone mode (defaults to local SQLite).
    return True


@lru_cache(maxsize=1)
def get_app_engine() -> Engine:
    url = _build_database_url()
    if _is_sqlite(url):
        db_path = url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            future=True,
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=config.APP_DB_POOL_SIZE,
        max_overflow=config.APP_DB_MAX_OVERFLOW,
        pool_timeout=config.APP_DB_POOL_TIMEOUT_SEC,
        pool_recycle=config.APP_DB_POOL_RECYCLE_SEC,
        future=True,
    )


def init_db() -> None:
    """Create all app tables if missing (standalone SQLite has no migrations)."""
    from db.models import Base  # local import to avoid circular import

    engine = get_app_engine()
    Base.metadata.create_all(engine)
    _ensure_columns(engine)


def _ensure_columns(engine: Engine) -> None:
    """Add columns introduced after a table was first created.

    create_all() never ALTERs an existing table, so new columns on long-lived
    tables (e.g. app_sessions.current_extract_run_id) must be added explicitly.
    Idempotent: skips columns that already exist.
    """
    from sqlalchemy import inspect, text

    required = {
        "app_sessions": {
            "current_extract_run_id": "VARCHAR(64)",
            "current_hl7_session_id": "VARCHAR(64)",
        },
        "hl7_sessions": {
            "app_session_id": "VARCHAR(64)",
            "user_key": "VARCHAR(255)",
        },
    }
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in required.items():
            if table not in existing_tables:
                continue  # create_all will have made it with the column
            present = {c["name"] for c in inspector.get_columns(table)}
            for col, ddl in columns.items():
                if col not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_app_engine(), autoflush=False, autocommit=False, future=True)


@contextmanager
def app_db_session() -> Session:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

