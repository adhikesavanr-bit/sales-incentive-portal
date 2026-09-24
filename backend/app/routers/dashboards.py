"""Dashboards. One router serves every level; the principal decides the scope.

There is no /api/team vs /api/zone split in the authorisation logic — the scope
predicate is derived from the caller, so a BDE hitting the roll-up endpoint sees
exactly one row: their own.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.deps import current_principal, require
from app.auth.rbac import Permission, Principal, visible_employee_sql
from app.models.schemas import MonthStatus
from app.services import dashboards, employees as employee_service, month

router = APIRouter(prefix="/api", tags=["dashboards"])


def _authorise_target(principal: Principal, employee_id: str | None) -> str:
    """Resolve which employee's data is being asked for, and prove it is allowed."""
    target = employee_id or principal.employee_id
    if target == principal.employee_id:
        return target
    scope, params = visible_employee_sql(principal)
    if not employee_service.is_in_scope(target, scope, params):
        # Deliberately the same message as a missing employee: a 403 that
        # distinguishes "exists but not yours" leaks the org chart.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No such employee in your reporting line."
        )
    return target


def _empty_state(employee_id: str, period: str) -> dict:
    """No row for this person and period.

    Still distinguishes an unpublished month from a published one with nothing
    in it, because the two need different wording, but says it in one line.
    """
    status_now = month.get_status(period)
    published = status_now is not MonthStatus.OPEN
    return {
        "period": period,
        "employee_id": employee_id,
        "status": "NO_SALES" if published else "NOT_CALCULATED",
        "month_status": status_now.value,
        "message": (
            "No records for this month."
            if published
            else "This month has not been published yet."
        ),
    }


@router.get("/me/dashboard")
def my_dashboard(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    row = dashboards.own(principal.employee_id, period)
    if row is None:
        return _empty_state(principal.employee_id, period)
    row["trend"] = dashboards.daily_trend([principal.employee_id], period)
    row["plan_mix"] = dashboards.plan_mix([principal.employee_id], period)
    return row


@router.get("/me/sales")
def my_sales(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    principal: Principal = Depends(current_principal),
):
    return dashboards.transactions(principal.employee_id, period, limit, offset)


@router.get("/me/coupons")
def my_coupons(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    """Each of my coupons for the month and whether it qualified."""
    return dashboards.coupon_analysis(principal.employee_id, period)


@router.get("/employees/{employee_id}/dashboard")
def employee_dashboard(
    employee_id: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    target = _authorise_target(principal, employee_id)
    row = dashboards.own(target, period)
    if row is None:
        return _empty_state(target, period)
    row["trend"] = dashboards.daily_trend([target], period)
    row["plan_mix"] = dashboards.plan_mix([target], period)
    return row


@router.get("/employees/{employee_id}/sales")
def employee_sales(
    employee_id: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    principal: Principal = Depends(current_principal),
):
    target = _authorise_target(principal, employee_id)
    return dashboards.transactions(target, period, limit, offset)


@router.get("/employees/{employee_id}/coupons")
def employee_coupons(
    employee_id: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    target = _authorise_target(principal, employee_id)
    return dashboards.coupon_analysis(target, period)


@router.get("/rollup/dashboard")
def rollup(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    group_by: str | None = Query(None, pattern=r"^(region|zone|submanager|rm)$"),
    principal: Principal = Depends(require(Permission.VIEW_TEAM)),
):
    """Team / region / zone / business view, scoped to the caller."""
    out = {
        "period": period,
        "scope": principal.role.value,
        "summary": dashboards.summary(principal, period),
        "employees": dashboards.team_rows(principal, period),
    }
    if group_by:
        out["groups"] = dashboards.group_by(principal, period, group_by)
    return out
