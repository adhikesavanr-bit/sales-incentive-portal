"""Who may read which incentive policy, and who may edit targets."""
import pytest

from app.auth.rbac import Permission, can_see_policy, has_permission
from app.models.schemas import Role

BDE, SUB, RM, ZM = "BDE_MONTHLY", "SUBMANAGER_MONTHLY", "TEAM_OWNER_QUARTERLY", "ZM_NINE_MONTH"
MBBS = "FIRST_YEAR_MBBS_UNITS"


@pytest.mark.parametrize("role, visible", [
    (Role.BDE, {BDE, MBBS}),
    (Role.SUB_MANAGER, {BDE, MBBS, SUB}),
    (Role.REGIONAL_MANAGER, {BDE, MBBS, SUB, RM}),
    (Role.TEAM_ADMIN, {BDE, MBBS, SUB, RM}),
    (Role.ZONAL_MANAGER, {BDE, MBBS, SUB, RM, ZM}),
    (Role.BUSINESS_HEAD, {BDE, MBBS, SUB, RM, ZM}),
    (Role.FINANCE_ADMIN, {BDE, MBBS, SUB, RM, ZM}),
    (Role.SUPER_ADMIN, {BDE, MBBS, SUB, RM, ZM}),
])
def test_each_level_sees_its_own_policy_and_below(role, visible):
    assert {s for s in (BDE, SUB, RM, ZM, MBBS) if can_see_policy(role, s)} == visible


@pytest.mark.parametrize("role", [
    Role.BDE, Role.SUB_MANAGER, Role.REGIONAL_MANAGER,
    Role.ZONAL_MANAGER, Role.BUSINESS_HEAD,
])
def test_sales_line_cannot_edit_targets(role):
    assert has_permission(role, Permission.VIEW_TARGETS)
    assert not has_permission(role, Permission.PROPOSE_TARGETS)
    assert not has_permission(role, Permission.APPROVE_TARGETS)


@pytest.mark.parametrize("role", [Role.TEAM_ADMIN, Role.FINANCE_ADMIN, Role.SUPER_ADMIN])
def test_admins_can_edit_targets(role):
    assert has_permission(role, Permission.PROPOSE_TARGETS)


def test_dashboard_results_are_reused_then_cleared_by_a_write(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import dashboards

    calls = []
    monkeypatch.setattr(dashboards.bq, "query",
                        lambda sql, params=None: calls.append(1) or [{"employee_id": "E1"}])
    dashboards.own("E1", "2026-08")
    row = dashboards.own("E1", "2026-08")
    row["mutated"] = True  # callers may add keys; the cached copy must not change
    assert dashboards.own("E1", "2026-08") == {"employee_id": "E1"}
    assert len(calls) == 1

    TestClient(app).post("/health")  # a write that fails leaves the cache alone
    dashboards.own("E1", "2026-08")
    assert len(calls) == 1

    dashboards.forget_results()
    dashboards.own("E1", "2026-08")
    assert len(calls) == 2
