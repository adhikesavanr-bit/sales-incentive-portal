"""Sign-in. Exchanges a Google ID token for a short-lived app session token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth.deps import current_principal, issue_app_token, verify_google_token
from app.auth.rbac import Principal
from app.config import get_settings
from app.services import audit, employees as employee_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/login", response_model=TokenResponse)
def login(body: GoogleLoginRequest, request: Request) -> TokenResponse:
    email = verify_google_token(body.id_token)
    token, ttl = issue_app_token(email)
    audit.record(
        email, "LOGIN",
        ip_address=request.client.host if request.client else None,
    )
    return TokenResponse(access_token=token, expires_in=ttl)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@router.post("/dev-login", response_model=TokenResponse)
def dev_login(request: Request) -> TokenResponse:
    """Sign in as DEV_LOGIN_EMAIL, for local development only.

    Three independent guards, because an auth bypass that escapes onto a
    deployed instance would hand anyone the whole payroll:

      1. The route 404s unless ALLOW_DEV_LOGIN is true.
      2. The caller must be on the loopback interface.
      3. The address must still exist in employee_master, so this grants no
         role that a real sign-in would not.

    Every use is written to the audit log under its own action.
    """
    s = get_settings()
    if not s.allow_dev_login or not s.dev_login_email:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")

    host = request.client.host if request.client else ""
    if host not in LOOPBACK:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Development sign-in is only available from this machine.",
        )

    email = s.dev_login_email.lower()
    if employee_service.get_by_email(email) is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{email} is not in the employee master.",
        )

    token, ttl = issue_app_token(email)
    audit.record(email, "DEV_LOGIN", ip_address=host,
                 reason="Local development sign-in")
    return TokenResponse(access_token=token, expires_in=ttl)


class MeResponse(BaseModel):
    employee_id: str
    full_name: str
    email: str
    role: str
    region: str | None
    zone: str | None
    vertical: str | None
    permissions: list[str]


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(current_principal)) -> MeResponse:
    return MeResponse(
        employee_id=principal.employee_id,
        full_name=principal.full_name,
        email=principal.email,
        role=principal.role.value,
        region=principal.region,
        zone=principal.zone,
        vertical=principal.vertical,
        permissions=sorted(p.value for p in principal.permissions),
    )
