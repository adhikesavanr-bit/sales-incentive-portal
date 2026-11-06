"""Dashboards. One router serves every level; the principal decides the scope.

There is no /api/team vs /api/zone split in the authorisation logic — the scope
predicate is derived from the caller, so a BDE hitting the roll-up endpoint sees
exactly one row: their own.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.deps import current_principal, require
from app.auth.rbac import Permission, Principal, visible_employee_sql
from app.models.schemas import Role
from app.services import dashboards, employees as employee_service, month

router = APIRouter(prefix="/api", tags=["dashboards"])

# A page's queries are independent, and each is a BigQuery round trip, so they
# run side by side rather than one after another.
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="dash")


def _person_dashboard(employee_id: str, period: str) -> dict:
    row_f = _pool.submit(dashboards.own, employee_id, period)
    trend_f = _pool.submit(dashboards.daily_trend, [employee_id], period)
    row = row_f.result()
    if row is None:
        trend_f.cancel()
        return _empty_state(employee_id, period)
    row["trend"] = trend_f.result()
    return row


def _authorise_target(principal: Principal, employee_id: str | None,
                      period: str | None = None) -> str:
    """Resolve which employee's data is being asked for, and prove it is allowed.

    Scope is checked against the hierarchy of the period being viewed.
    """
    target = employee_id or principal.employee_id
    if target == principal.employee_id:
        return target
    scope, params = visible_employee_sql(principal)
    if not employee_service.is_in_scope(target, scope, params, period):
        # Deliberately the same message as a missing employee: a 403 that
        # distinguishes "exists but not yours" leaks the org chart.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No such employee in your reporting line."
        )
    return target


def can_see_month(principal: Principal, period: str) -> bool:
    """Whether this caller may see the period's results yet.

    A month is published when it is APPROVED (or LOCKED). Until then only the
    people who calculate and review it see figures: Finance and super admins.
    Everyone else gets the "not published yet" state, on every page and export
    alike. "View as" shows exactly what that person sees, so an admin viewing
    as a BDE gets the BDE's "not published yet" too: the permissions are the
    viewed person's, not the admin's.
    """
    if principal.can(Permission.VIEW_UNPUBLISHED):
        return True
    return month.is_published(period)


def _not_published(employee_id: str, period: str) -> dict:
    return {
        "period": period,
        "employee_id": employee_id,
        "status": "NOT_PUBLISHED",
        "month_status": month.get_status(period).value,
        "message": month.not_published_message(period),
    }


def _mark_unpublished(row: dict, period: str) -> dict:
    """Tell a reviewer that what they are looking at is not public yet."""
    if not month.is_published(period):
        row["unpublished"] = True
        row["month_status"] = month.get_status(period).value
    return row


def _empty_state(employee_id: str, period: str) -> dict:
    """No row for this person and period.

    Still distinguishes an unpublished month from a published one with nothing
    in it, because the two need different wording, but says it in one line.
    """
    status_now = month.get_status(period)
    published = status_now in month.PUBLISHED
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
    if not can_see_month(principal, period):
        return _not_published(principal.employee_id, period)
    return _mark_unpublished(_person_dashboard(principal.employee_id, period), period)


def _scope_label(principal: Principal) -> str:
    """What the consolidated figures cover, in the caller's own terms."""
    if principal.role in (Role.FINANCE_ADMIN, Role.SUPER_ADMIN):
        return "Company"
    if principal.can(Permission.VIEW_BUSINESS):
        return principal.vertical or "Business"
    if principal.can(Permission.VIEW_ZONE):
        return f"{principal.zone} zone" if principal.zone else "Zone"
    if principal.can(Permission.VIEW_REGION):
        return f"{principal.region} region" if principal.region else "Region"
    return "Team"


@router.get("/me/consolidated")
def my_consolidated(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(require(Permission.VIEW_TEAM)),
):
    """The caller and everyone they can see, added up for the period."""
    if not can_see_month(principal, period):
        return {**_not_published(principal.employee_id, period),
                "scope_label": _scope_label(principal)}
    row_f = _pool.submit(dashboards.consolidated, principal, period)
    trend_f = _pool.submit(dashboards.scope_trend, principal, period)
    row = row_f.result()
    if row is None:
        trend_f.cancel()
        return {**_empty_state(principal.employee_id, period),
                "scope_label": _scope_label(principal)}
    row["scope_label"] = _scope_label(principal)
    if not principal.can(Permission.VIEW_BUSINESS):
        row.pop("payout_pct", None)
    row["trend"] = trend_f.result()
    return _mark_unpublished(row, period)


@router.get("/me/plan-summary")
def my_plan_summary(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(require(Permission.VIEW_BUSINESS)),
):
    """Plan-wise revenue and payments with the BDE-level rows to drill into.

    Business heads and finance/super admins only (VIEW_BUSINESS).
    """
    if not can_see_month(principal, period):
        return []
    return dashboards.plan_summary(principal, period)


@router.get("/me/sales")
def my_sales(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    principal: Principal = Depends(current_principal),
):
    if not can_see_month(principal, period):
        return []
    return dashboards.transactions(principal.employee_id, period, limit, offset)


@router.get("/me/coupons")
def my_coupons(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    """Each of my coupons for the month and whether it qualified."""
    if not can_see_month(principal, period):
        return []
    return dashboards.coupon_analysis(principal.employee_id, period)


@router.get("/employees/{employee_id}/dashboard")
def employee_dashboard(
    employee_id: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    target = _authorise_target(principal, employee_id, period)
    if not can_see_month(principal, period):
        return _not_published(target, period)
    return _mark_unpublished(_person_dashboard(target, period), period)


@router.get("/employees/{employee_id}/sales")
def employee_sales(
    employee_id: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    principal: Principal = Depends(current_principal),
):
    target = _authorise_target(principal, employee_id, period)
    if not can_see_month(principal, period):
        return []
    return dashboards.transactions(target, period, limit, offset)


@router.get("/employees/{employee_id}/coupons")
def employee_coupons(
    employee_id: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    target = _authorise_target(principal, employee_id, period)
    if not can_see_month(principal, period):
        return []
    return dashboards.coupon_analysis(target, period)


@router.get("/rollup/dashboard")
def rollup(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    group_by: str | None = Query(None, pattern=r"^(region|zone|submanager|rm)$"),
    principal: Principal = Depends(require(Permission.VIEW_TEAM)),
):
    """Team / region / zone / business view, scoped to the caller."""
    if not can_see_month(principal, period):
        gate = _not_published(principal.employee_id, period)
        out = {"period": period, "scope": principal.role.value,
               "status": gate["status"], "month_status": gate["month_status"],
               "message": gate["message"], "summary": {}, "employees": []}
        if group_by:
            out["groups"] = []
        return out
    summary_f = _pool.submit(dashboards.summary, principal, period)
    rows_f = _pool.submit(dashboards.team_rows, principal, period)
    groups_f = _pool.submit(dashboards.group_by, principal, period, group_by) if group_by else None
    out = {
        "period": period,
        "scope": principal.role.value,
        "summary": summary_f.result(),
        "employees": rows_f.result(),
    }
    if groups_f:
        out["groups"] = groups_f.result()
    return _mark_unpublished(out, period)


RANGE_ROLES = frozenset({Role.BUSINESS_HEAD, Role.FINANCE_ADMIN, Role.SUPER_ADMIN})


def _require_ranges(principal: Principal) -> None:
    """Quarter and year views are for business heads, Finance and super admins."""
    if principal.role not in RANGE_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Quarter and year views are not available for your role.")


def _months_between(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if not out or len(out) > 12:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A range covers 1 to 12 months, start before end.")
    return out


def load_range_months(principal: Principal, visible: list[str],
                      group_by: str | None = None) -> list[tuple[str, dict]]:
    """Each visible month's roll-up, oldest first, dropping months with no figures."""
    def load(m: str) -> dict:
        s_f = _pool.submit(dashboards.summary, principal, m)
        r_f = _pool.submit(dashboards.team_rows, principal, m)
        g_f = _pool.submit(dashboards.group_by, principal, m, group_by) if group_by else None
        return {"summary": s_f.result(), "employees": r_f.result(),
                "groups": g_f.result() if g_f else None}

    loaded = list(zip(visible, [load(m) for m in visible]))
    # A month with no calculated rows yet (e.g. in the future) adds nothing.
    return [(m, d) for m, d in loaded if any(e.get("target_units") is not None
            or e.get("total_incentive") is not None for e in d["employees"])]


@router.get("/rollup/range")
def rollup_range(
    start: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    end: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    group_by: str | None = Query(None, pattern=r"^(region|zone|submanager|rm)$"),
    principal: Principal = Depends(require(Permission.VIEW_TEAM)),
):
    """Quarter or financial-year view: the monthly roll-ups added up.

    Only months this caller may see are included, and the response says which
    were left out, so a total is never mistaken for a full period.
    """
    _require_ranges(principal)
    months = _months_between(start, end)
    visible = [m for m in months if can_see_month(principal, m)]

    with_data = load_range_months(principal, visible, group_by)
    out = {
        "period": f"{start}..{end}",
        "scope": principal.role.value,
        "months_included": [m for m, _ in with_data],
        "months_missing": [m for m in months if m not in {x for x, _ in with_data}],
        "is_range": True,
        **dashboards.merge_months([d for _, d in with_data]),
    }
    if not group_by:
        out.pop("groups", None)
    unpublished = [m for m, _ in with_data if not month.is_published(m)]
    if unpublished:
        out["unpublished"] = True
        out["month_status"] = month.get_status(unpublished[0]).value
    return out


@router.get("/me/consolidated/range")
def my_consolidated_range(
    start: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    end: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(require(Permission.VIEW_TEAM)),
):
    """My performance for a quarter or financial year (business head / Finance)."""
    _require_ranges(principal)
    months = _months_between(start, end)
    visible = [m for m in months if can_see_month(principal, m)]
    futures = {m: (_pool.submit(dashboards.consolidated, principal, m),
                   _pool.submit(dashboards.scope_trend, principal, m)) for m in visible}
    rows = [(m, c.result(), t.result()) for m, (c, t) in futures.items()]
    with_data = [(m, r, t) for m, r, t in rows if r is not None]
    base = {"period": f"{start}..{end}", "scope_label": _scope_label(principal),
            "is_range": True,
            "months_included": [m for m, _, _ in with_data],
            "months_missing": [m for m in months if m not in {x for x, _, _ in with_data}]}
    if not with_data:
        return {**base, "status": "NOT_CALCULATED",
                "message": "Nothing is published for this period yet."}
    out = {**base, **dashboards.merge_consolidated([r for _, r, _ in with_data]),
           "trend": [d for _, _, t in with_data for d in t]}
    unpublished = [m for m, _, _ in with_data if not month.is_published(m)]
    if unpublished:
        out["unpublished"] = True
        out["month_status"] = month.get_status(unpublished[0]).value
    return out
