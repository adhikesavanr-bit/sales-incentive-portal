"""Authentication: Google Workspace sign-in, then a short-lived app JWT.

Flow:
    Google ID token -> verified against Google's keys -> email
    email -> employee_master -> employee_id, role, hierarchy
    -> app JWT carrying ONLY the email (everything else is re-read per request)

The JWT deliberately does not carry the role or the employee id as the
authoritative source. They are re-resolved from BigQuery on every request, so
revoking access takes effect immediately rather than at token expiry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.auth.rbac import ROLE_PERMISSIONS, Permission, Principal
from app.config import get_settings
from app.models.schemas import Role
from app.services import employees as employee_service

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def verify_google_token(token: str) -> str:
    """Verify a Google ID token and return the verified email address."""
    s = get_settings()
    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), s.google_oauth_client_id
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed.") from exc

    if not claims.get("email_verified"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email address is not verified.")

    email = (claims.get("email") or "").lower()
    domain = email.split("@")[-1]
    if domain not in s.domains:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Sign in with a company account. {domain} is not permitted.",
        )
    return email


def issue_app_token(email: str) -> tuple[str, int]:
    s = get_settings()
    ttl = s.jwt_ttl_minutes
    payload = {
        "sub": email,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ttl),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), ttl * 60


# --- view as (impersonation) -----------------------------------------------
# A super admin can see the app exactly as another employee does. The token
# names the admin as `sub` and the person being viewed as `act_as`; both are
# re-checked on every request, so demoting the admin ends the session at once.
IMPERSONATION_TTL_MINUTES = 30
_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}
# Non-GET calls still allowed while viewing as someone: crash reports only.
_IMPERSONATION_WRITE_ALLOWLIST = {"/api/client-errors"}


def issue_impersonation_token(admin_email: str, employee_id: str) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": admin_email,
        "act_as": employee_id,
        "iat": now,
        "exp": now + timedelta(minutes=IMPERSONATION_TTL_MINUTES),
    }
    return (
        jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm),
        IMPERSONATION_TTL_MINUTES * 60,
    )


def _decode(token: str) -> dict:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Sign in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session token.")


def _principal_for(record, email: str, impersonator: str | None = None) -> Principal:
    role = record.role if isinstance(record.role, Role) else Role(record.role)
    return Principal(
        email=email,
        employee_id=record.employee_id,
        full_name=record.full_name,
        role=role,
        region=record.region,
        zone=record.zone,
        vertical=record.vertical,
        permissions=set(ROLE_PERMISSIONS.get(role, set())),
        impersonator=impersonator,
    )


def _enforce_read_only(request: Request, viewing: str) -> None:
    """Viewing as someone may look, never act — nothing is done in their name."""
    path = request.url.path
    blocked = (
        (request.method not in _READ_ONLY_METHODS
         and path not in _IMPERSONATION_WRITE_ALLOWLIST)
        or path.startswith("/api/export/")
    )
    if blocked:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"You are viewing as {viewing}, which is read-only. "
            "Exit view-as to make changes or export.",
        )


async def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    """Resolve the caller. Role and scope come from the database, not the token."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")

    claims = _decode(creds.credentials)
    email = claims["sub"]
    record = employee_service.get_by_email(email)
    if record is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This account is not in the employee master. Ask Finance to add it.",
        )
    if not record.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is inactive.")

    act_as = claims.get("act_as")
    if not act_as:
        principal = _principal_for(record, email)
    else:
        # Re-checked every request: an admin demoted mid-session loses it now.
        if _principal_for(record, email).role is not Role.SUPER_ADMIN:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "View-as has ended: only super admins can view as someone.",
            )
        target = employee_service.get_by_id(act_as)
        if target is None or not target.is_active:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "View-as has ended: that person is no longer active.",
            )
        _enforce_read_only(request, target.full_name)
        principal = _principal_for(target, (target.email or "").lower(), impersonator=email)

    request.state.principal = principal
    return principal


def require(*permissions: Permission):
    """Dependency factory: every route states the permission it needs."""

    async def _guard(principal: Principal = Depends(current_principal)) -> Principal:
        missing = [p for p in permissions if not principal.can(p)]
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Your role ({principal.role.value}) cannot {missing[0].value.lower().replace('_', ' ')}.",
            )
        return principal

    return _guard
