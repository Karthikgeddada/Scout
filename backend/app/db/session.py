"""Database engine/session setup (SQLAlchemy 2.x; SQLite by default, any
SQLAlchemy URL supported)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_shared_engine = None


def make_engine(url: str | None = None):
    url = url or get_settings().database_url
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # Allow FastAPI's threadpool to reuse the connection; FK cascades
        # (ondelete=) are enforced per-connection via the pragma below.
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def shared_engine():
    """Process-wide engine, created once."""
    global _shared_engine
    if _shared_engine is None:
        _shared_engine = make_engine()
        SessionLocal.configure(bind=_shared_engine)
    return _shared_engine


def init_db() -> None:
    """Create all tables if missing (idempotent; replaces Alembic)."""
    from app.db import models_chat, models_core, models_documents, models_jobs, models_retrieval  # noqa: F401

    Base.metadata.create_all(bind=shared_engine())


SessionLocal = sessionmaker(bind=None, autoflush=False, expire_on_commit=False, class_=Session)


def session_factory(engine) -> sessionmaker:
    SessionLocal.configure(bind=engine)
    return SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    session = session_factory(shared_engine())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed transactional session for background ingestion
    (commit on success, rollback on error). Uses the shared engine."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
