"""Authentication (Supabase JWT), rate limiting and authorization primitives.

JWT verification is JWKS-based against the project's Supabase instance; the
service-role key is never required to authenticate a request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import jwt

from app.core.errors import AuthenticationError, RateLimitError


class JWKSVerifier:
    """Fetches and caches Supabase JWKS; verifies RS256 access tokens."""

    def __init__(self, jwks_url: str, audience: str, fetch_json=None) -> None:
        # fetch_json is injectable for tests; production uses httpx.
        self._jwks_url = jwks_url
        self._audience = audience
        self._fetch_json = fetch_json
        self._keys: dict[str, str] = {}
        self._fetched_at = 0.0
        self._ttl = 3600.0

    def _load_keys(self) -> dict[str, str]:
        if self._keys and time.time() - self._fetched_at < self._ttl:
            return self._keys
        if self._fetch_json is None:
            import httpx  # imported lazily so the module stays unit-testable

            resp = httpx.get(self._jwks_url, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        else:
            payload = self._fetch_json(self._jwks_url)
        self._keys = {j["kid"]: j for j in payload.get("keys", [])}
        self._fetched_at = time.time()
        return self._keys

    def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Malformed token") from exc
        keys = self._load_keys()
        jwk = keys.get(header.get("kid"))
        if jwk is None:
            raise AuthenticationError("Unknown signing key")
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
        try:
            return jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=self._audience,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid or expired token") from exc


@dataclass
class TokenBucket:
    """Per-key fixed-window limiter (in-process; enough for per-user chat caps)."""

    limit: int
    window_seconds: int
    _hits: dict[str, list[float]] = field(default_factory=dict)

    def check(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        recent = [t for t in self._hits.get(key, []) if now - t < self.window_seconds]
        if len(recent) >= self.limit:
            raise RateLimitError("Too many requests; please retry shortly.")
        recent.append(now)
        self._hits[key] = recent


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Missing bearer token")
    return authorization.split(" ", 1)[1].strip()
