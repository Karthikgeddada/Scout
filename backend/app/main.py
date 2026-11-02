"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.observability import configure_logging, request_id_middleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="SCOUT API",
        version="1.0.0",
        description="AI-powered document intelligence platform",
        docs_url="/docs" if settings.api_docs_enabled else None,
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.middleware("http")(request_id_middleware)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "service": "scout-api", "version": "1.0.0"}

    return app


app = create_app()
