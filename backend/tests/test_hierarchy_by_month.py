"""The hierarchy is read as it stood in the month being viewed.

Oct 2026: NHP842 (R11 -> R13) and NHP536 (R13 -> R11) moved from September;
July and August must stay under their original region and manager.
"""
from datetime import date

import pytest
from fastapi import HTTPException

from app.auth.rbac import ROLE_PERMISSIONS, Principal
from app.models.schemas import Role
from app.routers import admin
from app.services import dashboards, employees, targets

RM = Principal(email="rm@x.com", employee_id="NHP248", full_name="Srinivasan",
               role=Role.REGIONAL_MANAGER, permissions=set(ROLE_PERMISSIONS[Role.REGIONAL_MANAGER]))


@pytest.fixture
def captured(monkeypatch):
    calls = []
    def fake_query(sql, params=None):
        calls.append((sql, params or {}))
        return []
    for mod in (dashboards, employees, targets):
        monkeypatch.setattr(mod.bq, "query", fake_query)
    dashboards.forget_results()
    return calls


@pytest.mark.parametrize("fn", [dashboards.team_rows, dashboards.summary,
                                dashboards.consolidated, dashboards.scope_trend])
def test_team_views_read_the_hierarchy_of_the_period(captured, fn):
    fn(RM, "2026-07")
    sql, params = captured[-1]
    assert "hierarchy_asof`(@p) h" in sql and params["p"] == "2026-07"
    assert "v_employee_hierarchy` h" not in sql


def test_by_region_groups_by_the_period_hierarchy(captured):
    dashboards.group_by(RM, "2026-08", "region")
    sql, params = captured[-1]
    assert "JOIN `test-project.test_dataset.hierarchy_asof`(@p) h" in sql
    assert params["p"] == "2026-08"


def test_targets_list_people_as_they_stood_that_month(captured):
    targets.in_scope(RM, "2026-07")
    assert "hierarchy_asof`(@p) h" in captured[-1][0]


def test_scope_check_uses_the_period_when_given(captured):
    employees.is_in_scope("NHP842", "h.rm_id = @scope_id", {"scope_id": "NHP248"}, "2026-08")
    sql, params = captured[-1]
    assert "hierarchy_asof`(@scope_period)" in sql and params["scope_period"] == "2026-08"
    employees.is_in_scope("NHP842", "h.rm_id = @scope_id", {"scope_id": "NHP248"})
    assert "v_employee_hierarchy" in captured[-1][0]


def test_the_calculation_reads_the_period_hierarchy(captured):
    employees.list_all(active_only=False, period="2026-09")
    sql, params = captured[-1]
    assert "hierarchy_asof`(@p)" in sql and params == {"p": "2026-09"}


# --- "Applies from" on People edits ------------------------------------------
def test_applies_from_is_the_first_of_the_month(monkeypatch):
    monkeypatch.setattr(admin.employee_service, "latest_effective_from", lambda i: date(2026, 7, 1))
    assert admin._effective_date("2026-09", "NHP842") == date(2026, 9, 1)
    assert admin._effective_date(None, "NHP842") is None


def test_a_change_cannot_be_dated_before_the_latest_one(monkeypatch):
    monkeypatch.setattr(admin.employee_service, "latest_effective_from", lambda i: date(2026, 9, 1))
    with pytest.raises(HTTPException) as e:
        admin._effective_date("2026-08", "NHP842")
    assert e.value.status_code == 409 and "September 2026" in e.value.detail


def test_upsert_closes_the_old_row_on_the_applies_from_date(monkeypatch):
    sent = {}
    monkeypatch.setattr(employees.bq, "query", lambda sql, p=None: sent.setdefault("close", p))
    monkeypatch.setattr(employees.bq, "append_rows", lambda t, rows: sent.setdefault("rows", rows))
    from app.models.schemas import Employee
    employees.upsert(Employee(employee_id="NHP536", full_name="Laxman"), "a@x", date(2026, 9, 1))
    assert sent["close"]["eff"] == date(2026, 9, 1)
    assert sent["rows"][0]["effective_from"] == "2026-09-01"
