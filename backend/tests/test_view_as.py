"""View as: a super admin seeing the app as someone else.

What must hold: only a super admin can start it; the viewed person's scope is
what applies; nothing can be written or exported; and it ends by itself.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.auth import deps
from app.config import get_settings
from app.models.schemas import Employee, Role

ADMIN = Employee(employee_id="NHP594", full_name="Admin", email="admin@dailyrounds.org",
                 role=Role.SUPER_ADMIN)
FINANCE = Employee(employee_id="NHP037", full_name="Finance", email="fin@dailyrounds.org",
                   role=Role.FINANCE_ADMIN)
RM = Employee(employee_id="NHP157", full_name="Sukanthan P Kathirvelu",
              email="sukanthan@marrowmed.com", role=Role.REGIONAL_MANAGER,
              region="R10-Sukanthan")
PEOPLE = {e.employee_id: e for e in (ADMIN, FINANCE, RM)}


@pytest.fixture(autouse=True)
def directory(monkeypatch):
    by_email = {e.email: e for e in PEOPLE.values()}
    monkeypatch.setattr(deps.employee_service, "get_by_email", lambda e: by_email.get(e))
    monkeypatch.setattr(deps.employee_service, "get_by_id", lambda i: PEOPLE.get(i))


def _call(token: str, method: str = "GET", path: str = "/api/me/dashboard"):
    request = Request({"type": "http", "method": method, "path": path, "headers": []})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    return asyncio.run(deps.current_principal(request, creds))


def _view_as_token(admin_email: str, employee_id: str, expired: bool = False) -> str:
    if not expired:
        return deps.issue_impersonation_token(admin_email, employee_id)[0]
    s = get_settings()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    return jwt.encode({"sub": admin_email, "act_as": employee_id, "exp": past},
                      s.jwt_secret, algorithm=s.jwt_algorithm)


class TestPrincipal:
    def test_the_viewed_persons_role_and_scope_apply(self):
        p = _call(_view_as_token(ADMIN.email, RM.employee_id))
        assert p.employee_id == "NHP157"
        assert p.role is Role.REGIONAL_MANAGER
        assert p.region == "R10-Sukanthan"
        assert p.impersonator == ADMIN.email

    def test_an_ordinary_token_is_unaffected(self):
        token, _ = deps.issue_app_token(RM.email)
        p = _call(token, method="POST", path="/api/targets")
        assert p.impersonator is None and p.employee_id == "NHP157"

    def test_a_non_super_admin_cannot_use_a_view_as_token(self):
        with pytest.raises(HTTPException) as e:
            _call(_view_as_token(FINANCE.email, RM.employee_id))
        assert e.value.status_code == 401

    def test_demoting_the_admin_ends_the_session_immediately(self, monkeypatch):
        token = _view_as_token(ADMIN.email, RM.employee_id)
        demoted = ADMIN.model_copy(update={"role": Role.FINANCE_ADMIN})
        monkeypatch.setattr(deps.employee_service, "get_by_email",
                            lambda e: demoted if e == ADMIN.email else None)
        with pytest.raises(HTTPException) as e:
            _call(token)
        assert e.value.status_code == 401

    def test_it_expires_after_thirty_minutes(self):
        assert deps.IMPERSONATION_TTL_MINUTES == 30
        with pytest.raises(HTTPException) as e:
            _call(_view_as_token(ADMIN.email, RM.employee_id, expired=True))
        assert e.value.status_code == 401


class TestReadOnly:
    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/targets"),
        ("PUT", "/api/rules/coupons"),
        ("POST", "/api/employees"),
        ("POST", "/api/sales/upload"),
        ("DELETE", "/api/anything"),
        ("GET", "/api/export/performance"),
        ("GET", "/api/export/statement"),
    ])
    def test_writes_and_exports_are_refused(self, method, path):
        with pytest.raises(HTTPException) as e:
            _call(_view_as_token(ADMIN.email, RM.employee_id), method, path)
        assert e.value.status_code == 403
        assert "read-only" in e.value.detail

    def test_reading_is_allowed(self):
        assert _call(_view_as_token(ADMIN.email, RM.employee_id), "GET", "/api/rollup/dashboard")

    def test_crash_reports_still_get_through(self):
        assert _call(_view_as_token(ADMIN.email, RM.employee_id), "POST", "/api/client-errors")


class TestStartAndEnd:
    @staticmethod
    def _principal(emp: Employee, impersonator=None):
        return deps._principal_for(emp, emp.email, impersonator=impersonator)

    @staticmethod
    def _request():
        return Request({"type": "http", "method": "POST", "path": "/api/auth/view-as",
                        "headers": [], "client": ("10.0.0.1", 1)})

    def test_super_admin_starts_it_and_it_is_audited(self, monkeypatch):
        from app.routers import auth
        logged = []
        monkeypatch.setattr(auth.audit, "record", lambda *a, **k: logged.append((a, k)))
        r = auth.start_view_as(auth.ViewAsRequest(employee_id="NHP157"),
                               self._request(), principal=self._principal(ADMIN))
        assert r.full_name == "Sukanthan P Kathirvelu" and r.expires_in == 1800
        assert logged[0][0] == (ADMIN.email, "VIEW_AS_START")
        assert logged[0][1]["affected_record"] == "NHP157"
        # and the token it hands back really is a read-only view as that person
        assert _call(r.access_token).employee_id == "NHP157"

    @pytest.mark.parametrize("who", [FINANCE, RM])
    def test_anyone_else_is_refused(self, who, monkeypatch):
        from app.routers import auth
        monkeypatch.setattr(auth.audit, "record", lambda *a, **k: None)
        with pytest.raises(HTTPException) as e:
            auth.start_view_as(auth.ViewAsRequest(employee_id="NHP157"),
                               self._request(), principal=self._principal(who))
        assert e.value.status_code == 403

    def test_no_nesting_view_as_from_inside_view_as(self, monkeypatch):
        from app.routers import auth
        monkeypatch.setattr(auth.audit, "record", lambda *a, **k: None)
        with pytest.raises(HTTPException):
            auth.start_view_as(auth.ViewAsRequest(employee_id="NHP037"), self._request(),
                               principal=self._principal(ADMIN, impersonator="x@y"))

    def test_unknown_or_own_id_is_refused(self, monkeypatch):
        from app.routers import auth
        monkeypatch.setattr(auth.audit, "record", lambda *a, **k: None)
        for emp_id, code in (("NHP999", 404), ("NHP594", 400)):
            with pytest.raises(HTTPException) as e:
                auth.start_view_as(auth.ViewAsRequest(employee_id=emp_id), self._request(),
                                   principal=self._principal(ADMIN))
            assert e.value.status_code == code

    def test_me_reports_who_is_viewing(self):
        from app.routers import auth
        out = auth.me(principal=self._principal(RM, impersonator=ADMIN.email))
        assert out.impersonated_by == ADMIN.email and out.employee_id == "NHP157"
