"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.core.config import get_settings
from app.core.observability import RequestContextMiddleware, configure_logging
from app.db.session import init_db


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.environment)
    init_db()  # create tables if missing (SQLite default; replaces Alembic)

    app = FastAPI(
        title="SCOUT API",
        version="1.0.0",
        description="AI-powered document intelligence platform",
        docs_url="/docs" if settings.api_docs_enabled else None,
    )

    # RequestContextMiddleware is added FIRST so CORS ends up outermost —
    # error responses it renders then still carry CORS headers.
    app.add_middleware(RequestContextMiddleware)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(router)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "service": "scout-api", "version": "1.0.0"}

    return app


app = create_app()
