"""A month's results reach the sales line only once the month is approved.

Until then Finance and super admins (and a super admin using "view as") see
everything, so they can review it; everyone else sees "not published yet" on
every page and export.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth.rbac import ROLE_PERMISSIONS, Permission, Principal
from app.models.schemas import MonthStatus, Role
from app.routers import dashboards as dash
from app.routers import exports
from app.services import month

ROW = {"employee_id": "NHP706", "period": "2026-09", "total_incentive": 12345.0}


def who(role: Role, impersonator: str | None = None) -> Principal:
    return Principal(email=f"{role.value.lower()}@x.com", employee_id="NHP706",
                     full_name="Someone", role=role,
                     permissions=set(ROLE_PERMISSIONS[role]), impersonator=impersonator)


@pytest.fixture
def status(monkeypatch):
    state = {"s": MonthStatus.UNDER_REVIEW}
    monkeypatch.setattr(month, "get_status", lambda p: state["s"])
    monkeypatch.setattr(dash, "_person_dashboard", lambda e, p: dict(ROW))
    monkeypatch.setattr(dash.dashboards, "transactions", lambda *a, **k: [{"payment_id": "p1"}])
    monkeypatch.setattr(dash.dashboards, "coupon_analysis", lambda *a, **k: [{"coupon": "ABJ01"}])
    monkeypatch.setattr(dash.dashboards, "summary", lambda *a: {"total_incentive": 1.0})
    monkeypatch.setattr(dash.dashboards, "team_rows", lambda *a: [dict(ROW)])
    monkeypatch.setattr(dash.employee_service, "is_in_scope", lambda *a: True)
    return state


SALES_LINE = [Role.BDE, Role.SUB_MANAGER, Role.REGIONAL_MANAGER, Role.ZONAL_MANAGER,
              Role.BUSINESS_HEAD, Role.TEAM_ADMIN]


@pytest.mark.parametrize("s", [MonthStatus.OPEN, MonthStatus.UNDER_REVIEW])
@pytest.mark.parametrize("role", SALES_LINE)
def test_the_sales_line_sees_nothing_before_approval(status, role, s):
    status["s"] = s
    p = who(role)
    out = dash.my_dashboard(period="2026-09", principal=p)
    assert out["status"] == "NOT_PUBLISHED" and "total_incentive" not in out
    assert out["message"] == "September 2026 not published yet"
    assert dash.my_sales(period="2026-09", limit=200, offset=0, principal=p) == []
    assert dash.my_coupons(period="2026-09", principal=p) == []
    other = dash.employee_dashboard("NHP845", period="2026-09", principal=p)
    assert other["status"] == "NOT_PUBLISHED"
    assert dash.employee_sales("NHP845", period="2026-09", limit=200, offset=0, principal=p) == []
    assert dash.employee_coupons("NHP845", period="2026-09", principal=p) == []
    roll = dash.rollup(period="2026-09", group_by=None, principal=p)
    assert roll["status"] == "NOT_PUBLISHED" and roll["employees"] == [] and roll["summary"] == {}


@pytest.mark.parametrize("role", SALES_LINE)
def test_exports_are_refused_before_approval(status, role):
    p = who(role)
    for call in (lambda: exports.export_statement(period="2026-09", employee_id=None, principal=p),
                 lambda: exports.export_report("incentive", period="2026-09", principal=p)):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 404 and e.value.detail == "September 2026 not published yet"


def test_consolidated_is_gated_too(status):
    out = dash.my_consolidated(period="2026-09", principal=who(Role.REGIONAL_MANAGER))
    assert out["status"] == "NOT_PUBLISHED" and out["scope_label"]


@pytest.mark.parametrize("s", [MonthStatus.APPROVED, MonthStatus.LOCKED])
def test_everyone_sees_it_once_approved(status, s):
    status["s"] = s
    out = dash.my_dashboard(period="2026-09", principal=who(Role.BDE))
    assert out["total_incentive"] == 12345.0 and "unpublished" not in out
    assert dash.my_sales(period="2026-09", limit=200, offset=0, principal=who(Role.BDE))


@pytest.mark.parametrize("role", [Role.FINANCE_ADMIN, Role.SUPER_ADMIN])
def test_reviewers_see_it_before_approval_marked_unpublished(status, role):
    out = dash.my_dashboard(period="2026-09", principal=who(role))
    assert out["total_incentive"] == 12345.0
    assert out["unpublished"] is True and out["month_status"] == "UNDER_REVIEW"
    roll = dash.rollup(period="2026-09", group_by=None, principal=who(role))
    assert roll["employees"] and roll["unpublished"] is True


def test_a_super_admin_viewing_as_a_bde_still_sees_it(status):
    p = who(Role.BDE, impersonator="admin@dailyrounds.org")
    assert dash.my_dashboard(period="2026-09", principal=p)["total_incentive"] == 12345.0


def test_only_finance_and_super_admin_hold_the_permission():
    holders = {r for r, perms in ROLE_PERMISSIONS.items() if Permission.VIEW_UNPUBLISHED in perms}
    assert holders == {Role.FINANCE_ADMIN, Role.SUPER_ADMIN}


def test_a_status_change_is_seen_at_once_on_this_instance(monkeypatch):
    calls = []
    monkeypatch.setattr(month.bq, "query",
                        lambda *a, **k: calls.append(1) or [{"status": "UNDER_REVIEW"}])
    monkeypatch.setattr(month.bq, "insert_rows", lambda *a, **k: None)
    month.forget_statuses()
    assert month.get_status("2026-09") is MonthStatus.UNDER_REVIEW
    assert month.get_status("2026-09") is MonthStatus.UNDER_REVIEW
    assert len(calls) == 1                                  # second read cached
    month.transition("2026-09", MonthStatus.APPROVED, "fin@x.com", "approved")
    monkeypatch.setattr(month.bq, "query", lambda *a, **k: [{"status": "APPROVED"}])
    assert month.is_published("2026-09")
    month.forget_statuses()
