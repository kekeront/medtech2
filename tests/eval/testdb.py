"""Isolated test database for the eval pipeline.

A dedicated engine bound to `medarchive_test` (override with TEST_DATABASE_URL).
`reset()` drops and recreates every table so each eval cycle starts clean; the
working `medarchive` DB is never touched. The same engine can be wired into the
FastAPI app via `override_api_session` for in-process GET checks.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Partner  # noqa: F401 — ensure all mappers are registered

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://medarchive:medarchive@localhost:5432/medarchive_test",
)

engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
TestSessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, future=True
)


def reset() -> None:
    """Drop and recreate the whole schema — a guaranteed-clean slate."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def is_empty() -> bool:
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM price_items")).scalar()
    return not n


def get_test_session():
    """FastAPI dependency override: yield a session bound to the test DB."""
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def override_api_session(app) -> None:
    """Point the FastAPI app's DB dependency at the test database."""
    from app.api import get_session

    app.dependency_overrides[get_session] = get_test_session
