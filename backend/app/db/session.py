"""Database engine/session setup (SQLAlchemy 2.x, psycopg3)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    return create_engine(url or get_settings().database_url, pool_pre_ping=True, future=True)


SessionLocal = sessionmaker(bind=None, autoflush=False, expire_on_commit=False, class_=Session)


def session_factory(engine) -> sessionmaker:
    SessionLocal.configure(bind=engine)
    return SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    settings = get_settings()
    factory = session_factory(make_engine(settings.database_url))
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_shared_engine = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed transactional session for workers (commit on success,
    rollback on error). Binds a process-wide engine on first use."""
    global _shared_engine
    if _shared_engine is None:
        _shared_engine = make_engine(get_settings().database_url)
        SessionLocal.configure(bind=_shared_engine)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
