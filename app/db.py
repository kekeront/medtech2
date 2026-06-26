"""SQLAlchemy engine / session / declarative base."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, future=True
)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables. Idempotent — safe to call on every startup."""
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
