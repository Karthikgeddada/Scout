"""Structured logging, request IDs, timing and error rendering."""

from __future__ import annotations

import contextvars
import time
import uuid

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.errors import ScoutError, error_payload

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def configure_logging(environment: str) -> None:
    """JSON logs in staging/production, console logs locally."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
            if environment != "development"
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)


class RequestContextMiddleware:
    """Assigns a request id, logs the call and renders ScoutError as JSON."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        log = get_logger("request")
        start = time.perf_counter()
        try:
            await self.app(scope, receive, send)
        except ScoutError as exc:
            response = JSONResponse(
                status_code=exc.status, content=error_payload(exc, request_id=rid)
            )
            await response(scope, receive, send)
        except Exception as exc:  # noqa: BLE001 - render unexpected errors consistently
            log.exception("unhandled_error", path=request.url.path, error=str(exc))
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "An unexpected error occurred.",
                        "details": None,
                        "request_id": rid,
                    }
                },
            )
            await response(scope, receive, send)
        finally:
            if scope.get("_response_status") is not None:
                pass
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status=scope.get("_response_status", "-"),
                duration_ms=duration_ms,
                request_id=rid,
            )
            request_id_var.reset(token)
