"""Payout % and the plan-wise drill-down are for business-level callers only."""
import pytest
from fastapi import HTTPException

from app.auth.rbac import Permission, Principal, has_permission
from app.models.schemas import Role
from app.routers import dashboards as router
from app.services import dashboards


def _principal(role: Role) -> Principal:
    perms = {p for p in Permission if has_permission(role, p)}
    return Principal(email="x@y.z", employee_id="E1", full_name="X", role=role,
                     vertical="V", permissions=perms)


def test_payout_pct_is_incentive_over_qualified_plus_disqualified(monkeypatch):
    row = {"people": 2, "headcount": 2, "total_incentive": 30.0,
           "qualified_revenue": 900.0, "disqualified_revenue": 100.0}
    monkeypatch.setattr(dashboards.bq, "query", lambda sql, params=None: [row])
    dashboards.forget_results()
    out = dashboards.consolidated(_principal(Role.BUSINESS_HEAD), "2026-08")
    assert out["payout_pct"] == pytest.approx(0.03)


def test_payout_pct_is_zero_without_revenue(monkeypatch):
    monkeypatch.setattr(dashboards.bq, "query",
                        lambda sql, params=None: [{"people": 1, "total_incentive": 5}])
    dashboards.forget_results()
    assert dashboards.consolidated(_principal(Role.FINANCE_ADMIN), "2026-08")["payout_pct"] == 0.0


@pytest.mark.parametrize("role", [Role.BDE, Role.REGIONAL_MANAGER, Role.ZONAL_MANAGER])
def test_plan_summary_needs_business_level(role):
    assert not has_permission(role, Permission.VIEW_BUSINESS)


@pytest.mark.parametrize("role", [Role.BUSINESS_HEAD, Role.FINANCE_ADMIN, Role.SUPER_ADMIN])
def test_plan_summary_open_to_business_level(role, monkeypatch):
    assert has_permission(role, Permission.VIEW_BUSINESS)
    monkeypatch.setattr(router, "can_see_month", lambda p, period: True)
    monkeypatch.setattr(router.dashboards, "plan_summary", lambda p, period: [{"plan_title": "A"}])
    assert router.my_plan_summary("2026-08", _principal(role)) == [{"plan_title": "A"}]
