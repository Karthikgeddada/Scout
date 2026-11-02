"""Typed application errors mapped to consistent JSON responses."""

from __future__ import annotations

from typing import Any


class ScoutError(Exception):
    """Base class for all expected SCOUT errors."""

    code = "scout_error"
    status = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(ScoutError):
    code = "not_found"
    status = 404


class AuthorizationError(ScoutError):
    code = "forbidden"
    status = 403


class AuthenticationError(ScoutError):
    code = "unauthorized"
    status = 401


class RequestValidationError(ScoutError):
    code = "validation_error"
    status = 422


class RateLimitError(ScoutError):
    code = "rate_limited"
    status = 429


class StorageError(ScoutError):
    code = "storage_error"
    status = 502


class IngestionError(ScoutError):
    code = "ingestion_error"
    status = 422


class UpstreamError(ScoutError):
    code = "upstream_error"
    status = 502


class ConflictError(ScoutError):
    code = "conflict"
    status = 409


def error_payload(error: ScoutError, request_id: str | None = None) -> dict:
    """Consistent error contract used by the exception handler."""
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
            "request_id": request_id,
        }
    }
