"""FastAPI dependencies: authentication, workspace authorization, rate limiting."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.security import JWKSVerifier, TokenBucket, extract_bearer_token
from app.db.models_core import Profile, Workspace, WorkspaceMember
from app.db.session import get_session

_verifier: JWKSVerifier | None = None
_rate_limiter: TokenBucket | None = None


def verifier(settings: Settings | None = None) -> JWKSVerifier:
    global _verifier
    if _verifier is None:
        s = settings or get_settings()
        # Derive the JWKS URL from the project URL unless explicitly set.
        jwks = s.supabase_jwks_url or f"{s.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _verifier = JWKSVerifier(jwks, s.supabase_jwt_audience)
    return _verifier


def rate_limiter() -> TokenBucket:
    global _rate_limiter
    if _rate_limiter is None:
        s = get_settings()
        _rate_limiter = TokenBucket(s.rate_limit_requests, s.rate_limit_window_seconds)
    return _rate_limiter


@dataclass
class CurrentUser:
    profile: Profile
    supabase_claims: dict


async def get_current_user(request: Request, session: Session = Depends(get_session)) -> CurrentUser:
    """Verify the Supabase JWT, load/ensure the profile row, apply rate limits."""
    rate_limiter().check(extract_bearer_token(request.headers.get("authorization")))
    claims = verifier().verify(extract_bearer_token(request.headers.get("authorization")))
    sub = claims["sub"]
    profile = session.scalar(select(Profile).where(Profile.supabase_user_id == sub))
    if profile is None:
        profile = Profile(
            supabase_user_id=sub,
            email=claims.get("email"),
            display_name=claims.get("user_metadata", {}).get("name"),
        )
        session.add(profile)
        session.flush()
    return CurrentUser(profile=profile, supabase_claims=claims)


def require_workspace(
    workspace_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Workspace:
    """Workspace-level authorization: caller must be a member."""
    import uuid as _uuid

    try:
        ws_id = _uuid.UUID(workspace_id)
    except ValueError:
        raise AuthorizationError("invalid workspace id")
    ws = session.get(Workspace, ws_id)
    if ws is None:
        raise AuthorizationError("workspace not found")
    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.profile_id == user.profile.id,
        )
    )
    if member is None:
        raise AuthorizationError("not a member of this workspace")
    return ws
