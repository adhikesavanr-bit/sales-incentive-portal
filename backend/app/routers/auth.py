"""Sign-in. Exchanges a Google ID token for a short-lived app session token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.auth.deps import (
    current_principal,
    issue_app_token,
    issue_impersonation_token,
    verify_google_token,
)
from app.auth.rbac import Principal
from app.config import get_settings
from app.models.schemas import Role
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


class PublicConfig(BaseModel):
    google_client_id: str
    allowed_email_domains: list[str]


@router.get("/config", response_model=PublicConfig)
def public_config() -> PublicConfig:
    """Client configuration the sign-in page needs before anyone is signed in.

    Served at runtime rather than compiled into the frontend bundle. Next.js
    inlines NEXT_PUBLIC_* at build time, which means a Dockerfile build needs
    the value as a --build-arg — easy to get wrong, and it makes rotating the
    OAuth client a rebuild rather than a restart. None of this is secret: the
    client id is visible in the page source of every Google sign-in on the web.
    """
    s = get_settings()
    return PublicConfig(
        google_client_id=s.google_oauth_client_id,
        allowed_email_domains=s.domains,
    )


class MeResponse(BaseModel):
    employee_id: str
    full_name: str
    email: str
    role: str
    region: str | None
    zone: str | None
    vertical: str | None
    permissions: list[str]
    # The super admin viewing as this person, when that is what is happening.
    impersonated_by: str | None = None


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
        impersonated_by=principal.impersonator,
    )


# --- view as ----------------------------------------------------------------

class ViewAsRequest(BaseModel):
    employee_id: str


class ViewAsResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    employee_id: str
    full_name: str
    role: str


def _require_super_admin(principal: Principal) -> None:
    if principal.impersonator or principal.role is not Role.SUPER_ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only super admins can view as someone."
        )


@router.post("/view-as", response_model=ViewAsResponse)
def start_view_as(
    body: ViewAsRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> ViewAsResponse:
    """See the app exactly as another employee does. Read-only, 30 minutes."""
    _require_super_admin(principal)
    target = employee_service.get_by_id(body.employee_id)
    if target is None or not target.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active employee with that ID.")
    if target.employee_id == principal.employee_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That is your own account.")

    token, ttl = issue_impersonation_token(principal.email, target.employee_id)
    role = target.role.value if isinstance(target.role, Role) else str(target.role)
    audit.record(
        principal.email, "VIEW_AS_START", entity_type="employee",
        affected_record=target.employee_id,
        new_value={"full_name": target.full_name, "role": role, "minutes": ttl // 60},
        reason="Read-only view of the app as this employee",
        ip_address=request.client.host if request.client else None,
    )
    return ViewAsResponse(
        access_token=token, expires_in=ttl, employee_id=target.employee_id,
        full_name=target.full_name, role=role,
    )


@router.post("/view-as/end", status_code=status.HTTP_204_NO_CONTENT,
             response_class=Response)
def end_view_as(
    body: ViewAsRequest,
    principal: Principal = Depends(current_principal),
) -> Response:
    """Record the end of a view-as session. Called with the admin's own token."""
    _require_super_admin(principal)
    audit.record(
        principal.email, "VIEW_AS_END", entity_type="employee",
        affected_record=body.employee_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
