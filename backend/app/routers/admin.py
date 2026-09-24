"""Employees, hierarchy, targets, incentive rules, recalculation, month lock."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.deps import current_principal, require
from app.auth.rbac import Permission, Principal, visible_employee_sql
from app.config import get_settings
from app.db import bigquery as bq
from app.models.schemas import Employee, MonthStatus, Role
from app.services import (
    audit,
    coupon_rules,
    employees as employee_service,
    incentive_run,
    month,
    source_tables,
)

router = APIRouter(prefix="/api", tags=["admin"])


# --- employees -------------------------------------------------------------
@router.get("/employees")
def list_employees(
    include_inactive: bool = Query(False),
    principal: Principal = Depends(current_principal),
):
    scope, params = visible_employee_sql(principal)
    people = employee_service.list_in_scope(scope, params)
    if not include_inactive:
        people = [p for p in people if p.is_active]
    return people


@router.get("/employees/roles")
def assignable_roles(
    principal: Principal = Depends(require(Permission.MANAGE_EMPLOYEES)),
):
    """Roles this caller may hand out.

    Nobody can create a role more powerful than their own, so a team admin
    cannot promote someone to Finance and thereby see company-wide pay.
    """
    everyday = [Role.BDE, Role.SUB_MANAGER, Role.REGIONAL_MANAGER,
                Role.ZONAL_MANAGER, Role.TEAM_ADMIN]
    if principal.role in (Role.SUPER_ADMIN, Role.FINANCE_ADMIN):
        everyday += [Role.BUSINESS_HEAD, Role.FINANCE_ADMIN]
    if principal.role is Role.SUPER_ADMIN:
        everyday += [Role.SUPER_ADMIN]
    return [
        {"value": r.value, "label": r.value.replace("_", " ").title()}
        for r in everyday
    ]


@router.post("/employees", status_code=status.HTTP_201_CREATED)
def create_employee(
    employee: Employee,
    reason: str = Query("Created via admin console"),
    principal: Principal = Depends(require(Permission.MANAGE_EMPLOYEES)),
):
    if employee_service.get_by_id(employee.employee_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{employee.employee_id} already exists. Edit it instead.",
        )
    employee_service.upsert(employee, principal.email)
    employee_service.set_hierarchy(employee, principal.email)
    audit.record(
        principal.email, "EMPLOYEE_CREATE", entity_type="employee",
        affected_record=employee.employee_id,
        new_value=employee.model_dump(), reason=reason,
    )
    return employee


@router.put("/employees/{employee_id}")
def update_employee(
    employee_id: str,
    employee: Employee,
    reason: str = Query(..., min_length=3),
    principal: Principal = Depends(require(Permission.MANAGE_EMPLOYEES)),
):
    existing = employee_service.get_by_id(employee_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No employee {employee_id}.")
    employee.employee_id = employee_id
    employee_service.upsert(employee, principal.email)
    employee_service.set_hierarchy(employee, principal.email)
    audit.record(
        principal.email, "EMPLOYEE_UPDATE", entity_type="employee",
        affected_record=employee_id,
        old_value=existing.model_dump(), new_value=employee.model_dump(), reason=reason,
    )
    return employee


class DeactivateIn(BaseModel):
    exit_date: date
    reason: str = Field(..., min_length=3)


@router.post("/employees/{employee_id}/deactivate")
def deactivate_employee(
    employee_id: str,
    body: DeactivateIn,
    principal: Principal = Depends(require(Permission.MANAGE_EMPLOYEES)),
):
    """Record that someone has left.

    Their record is closed, not deleted: past months still need an owner for
    every sale, and the audit trail has to stay readable.
    """
    existing = employee_service.get_by_id(employee_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No employee {employee_id}.")

    scope, sparams = visible_employee_sql(principal)
    if not employee_service.is_in_scope(employee_id, scope, sparams):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No such employee in your reporting line."
        )

    updated = employee_service.deactivate(employee_id, body.exit_date, principal.email)
    audit.record(
        principal.email, "EMPLOYEE_DEACTIVATE", entity_type="employee",
        affected_record=employee_id,
        old_value={"is_active": True},
        new_value={"is_active": False, "exit_date": body.exit_date.isoformat()},
        reason=body.reason,
    )
    return updated


@router.post("/employees/{employee_id}/reactivate")
def reactivate_employee(
    employee_id: str,
    reason: str = Query(..., min_length=3),
    principal: Principal = Depends(require(Permission.MANAGE_EMPLOYEES)),
):
    existing = employee_service.get_by_id(employee_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No employee {employee_id}.")
    existing.is_active = True
    existing.exit_date = None
    employee_service.upsert(existing, principal.email)
    audit.record(
        principal.email, "EMPLOYEE_REACTIVATE", entity_type="employee",
        affected_record=employee_id, new_value={"is_active": True}, reason=reason,
    )
    return existing


# --- targets ---------------------------------------------------------------
class TargetUpsert(BaseModel):
    employee_id: str
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    target_units: float = Field(..., ge=0)
    winner_units: float | None = None
    reason: str = Field(..., min_length=3)


@router.get("/targets")
def list_targets(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    s = get_settings()
    scope, params = visible_employee_sql(principal)
    # LEFT JOIN from people to targets, so someone with no target for the
    # period still appears with a blank row to fill in. The previous inner
    # join is why a fresh month looked empty.
    return bq.query(
        f"""
        SELECT h.employee_id, h.full_name, h.region, h.zone, h.designation,
               t.period, t.target_units, t.winner_units, t.status, t.version
        FROM {s.table('v_employee_hierarchy')} h
        LEFT JOIN {s.table('targets')} t
          ON t.employee_id = h.employee_id AND t.period = @p
        WHERE h.is_active AND {scope}
        ORDER BY h.region, h.full_name
        """,
        {**params, "p": period},
    )


@router.post("/targets")
def upsert_target(
    body: TargetUpsert,
    principal: Principal = Depends(require(Permission.PROPOSE_TARGETS)),
):
    """RMs propose; only an approver's submission lands as APPROVED."""
    month.require_open(body.period)
    s = get_settings()

    old = bq.query(
        f"SELECT * FROM {s.table('targets')} "
        "WHERE employee_id = @e AND period = @p "
        "ORDER BY version DESC LIMIT 1",
        {"e": body.employee_id, "p": body.period},
    )
    version = int(old[0]["version"]) + 1 if old else 1

    approver = principal.can(Permission.APPROVE_TARGETS)
    # Winner units are Base x 1.3 in every month of the supplied workbook.
    winner = body.winner_units if body.winner_units is not None else body.target_units * 1.3
    now = datetime.now(timezone.utc).isoformat()

    bq.append_rows("targets", [{
        "employee_id": body.employee_id,
        "period": body.period,
        "vertical": "Marrow",
        "target_units": body.target_units,
        "winner_units": winner,
        "status": "APPROVED" if approver else "SUBMITTED",
        "submitted_by": principal.email,
        "approved_by": principal.email if approver else None,
        "approved_at": now if approver else None,
        "version": version,
        "updated_at": now,
    }])
    audit.record(
        principal.email, "TARGET_UPDATE", entity_type="target",
        affected_record=f"{body.employee_id}:{body.period}",
        old_value=old[0] if old else None,
        new_value={"target_units": body.target_units, "winner_units": winner},
        reason=body.reason,
    )
    return {"employee_id": body.employee_id, "period": body.period,
            "target_units": body.target_units, "winner_units": winner,
            "status": "APPROVED" if approver else "SUBMITTED", "version": version}


@router.post("/targets/{employee_id}/approve")
def approve_target(
    employee_id: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    reason: str = Query(..., min_length=3),
    principal: Principal = Depends(require(Permission.APPROVE_TARGETS)),
):
    s = get_settings()
    bq.query(
        f"UPDATE {s.table('targets')} SET status = 'APPROVED', "
        "approved_by = @by, approved_at = CURRENT_TIMESTAMP() "
        "WHERE employee_id = @e AND period = @p AND status = 'SUBMITTED'",
        {"by": principal.email, "e": employee_id, "p": period},
    )
    audit.record(
        principal.email, "TARGET_APPROVE", entity_type="target",
        affected_record=f"{employee_id}:{period}", reason=reason,
    )
    return {"employee_id": employee_id, "period": period, "status": "APPROVED"}


# --- incentive rules -------------------------------------------------------
@router.get("/incentive/rules")
def list_rules(principal: Principal = Depends(current_principal)):
    s = get_settings()
    return bq.query(
        f"SELECT * FROM {s.table('incentive_rules')} ORDER BY scope, threshold"
    )


@router.post("/incentive/rules")
def create_rule(
    rule: dict = Body(...),
    reason: str = Query(..., min_length=3),
    principal: Principal = Depends(require(Permission.MANAGE_RULES)),
):
    rule["rule_id"] = str(uuid.uuid4())
    rule["created_by"] = principal.email
    rule["created_at"] = datetime.now(timezone.utc).isoformat()
    bq.append_rows("incentive_rules", [rule])
    audit.record(
        principal.email, "RULE_CREATE", entity_type="incentive_rule",
        affected_record=rule["rule_id"], new_value=rule, reason=reason,
    )
    return rule


# --- coupon qualification rules -------------------------------------------
class CouponRuleIn(BaseModel):
    group_size: str = Field(..., pattern=r"^G\d+$")
    required_sales: int = Field(..., ge=1)
    min_sales: int = Field(..., ge=1)
    min_own_sales: int = Field(..., ge=0)
    effective_from: date
    reason: str = Field(..., min_length=3)


@router.get("/rules/coupons")
def list_coupon_rules(
    period: str = Query(None, pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    """The thresholds in force for a period, defaulting to this month."""
    target = period or datetime.now(timezone.utc).strftime("%Y-%m")
    return {
        "period": target,
        "rules": coupon_rules.for_period(target),
        "editable": principal.can(Permission.MANAGE_RULES),
    }


@router.get("/rules/coupons/history")
def coupon_rule_history(
    group_size: str | None = Query(None, pattern=r"^G\d+$"),
    principal: Principal = Depends(require(Permission.MANAGE_RULES)),
):
    return coupon_rules.history(group_size)


@router.put("/rules/coupons")
def update_coupon_rule(
    body: CouponRuleIn,
    principal: Principal = Depends(require(Permission.MANAGE_RULES)),
):
    """Write a new version of one group size's thresholds.

    Refused if it would change a month that is already approved or locked: the
    numbers in those months have been reported, and a rule edit must not
    silently reach back into them.
    """
    changed_period = body.effective_from.strftime("%Y-%m")
    status_then = month.get_status(changed_period)
    if status_then in (MonthStatus.APPROVED, MonthStatus.LOCKED):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{changed_period} is {status_then.value.lower()}. Choose a later "
            f"effective date, or reopen that month first.",
        )

    before = {r.group_size: r for r in coupon_rules.for_period(changed_period)}
    updated = coupon_rules.update(
        body.group_size, body.required_sales, body.min_sales,
        body.min_own_sales, body.effective_from, body.reason, principal.email,
    )
    audit.record(
        principal.email, "COUPON_RULE_UPDATE", entity_type="coupon_rule",
        affected_record=body.group_size,
        old_value=before.get(body.group_size).model_dump()
        if body.group_size in before else None,
        new_value=updated.model_dump(), reason=body.reason,
    )
    return updated


class SlabIn(BaseModel):
    scope: str
    threshold: float = Field(..., ge=0)
    rate: float = Field(..., ge=0, le=1)
    effective_from: date
    reason: str = Field(..., min_length=3)


@router.get("/rules/slabs")
def list_slabs(
    period: str = Query(None, pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(current_principal),
):
    """Achievement slabs in force for a period, grouped by scope."""
    target = period or datetime.now(timezone.utc).strftime("%Y-%m")
    s = get_settings()
    rows = bq.query(
        f"""
        SELECT scope, threshold, rate, effective_from, source_note
        FROM {s.table('incentive_rules')}
        WHERE effective_from <= LAST_DAY(PARSE_DATE('%Y-%m', @p))
          AND (effective_to IS NULL OR effective_to >= PARSE_DATE('%Y-%m', @p))
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY scope, threshold ORDER BY effective_from DESC
        ) = 1
        ORDER BY scope, threshold
        """,
        {"p": target},
    )
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["scope"], []).append(r)
    return {
        "period": target,
        "scopes": grouped,
        "editable": principal.can(Permission.MANAGE_RULES),
    }


@router.put("/rules/slabs")
def update_slab(
    body: SlabIn,
    principal: Principal = Depends(require(Permission.MANAGE_RULES)),
):
    """Change the rate on one slab, effective from a date.

    Same protection as the coupon rules: an edit landing in an approved or
    locked month is refused rather than silently changing a reported figure.
    """
    changed_period = body.effective_from.strftime("%Y-%m")
    status_then = month.get_status(changed_period)
    if status_then in (MonthStatus.APPROVED, MonthStatus.LOCKED):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{changed_period} is {status_then.value.lower()}. Choose a later "
            f"effective date, or reopen that month first.",
        )

    s = get_settings()
    eff = body.effective_from.isoformat()
    old = bq.query(
        f"SELECT rate FROM {s.table('incentive_rules')} "
        "WHERE scope = @s AND threshold = @t AND effective_to IS NULL LIMIT 1",
        {"s": body.scope, "t": body.threshold},
    )
    bq.query(
        f"UPDATE {s.table('incentive_rules')} SET effective_to = DATE(@eff) "
        "WHERE scope = @s AND threshold = @t AND effective_to IS NULL "
        "  AND effective_from < DATE(@eff)",
        {"s": body.scope, "t": body.threshold, "eff": eff},
    )
    bq.query(
        f"DELETE FROM {s.table('incentive_rules')} "
        "WHERE scope = @s AND threshold = @t AND effective_from = DATE(@eff)",
        {"s": body.scope, "t": body.threshold, "eff": eff},
    )
    bq.append_rows("incentive_rules", [{
        "rule_id": str(uuid.uuid4()),
        "scope": body.scope,
        "vertical": "Marrow",
        "threshold": body.threshold,
        "rate": body.rate,
        "effective_from": eff,
        "source_note": body.reason,
        "created_by": principal.email,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }])
    audit.record(
        principal.email, "SLAB_UPDATE", entity_type="incentive_rule",
        affected_record=f"{body.scope}@{body.threshold}",
        old_value={"rate": float(old[0]["rate"])} if old else None,
        new_value={"rate": body.rate}, reason=body.reason,
    )
    return {"scope": body.scope, "threshold": body.threshold, "rate": body.rate,
            "effective_from": eff}


# --- source tables ---------------------------------------------------------
class SourceTableIn(BaseModel):
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    dataset: str
    table: str
    project: str | None = None
    date_column: str = "payment_date_ist"
    column_map: dict[str, str] | None = None
    reason: str = Field(..., min_length=3)


@router.get("/sources")
def list_sources(
    period: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    """Every source-table decision ever made, newest first."""
    return source_tables.history(period)


@router.get("/sources/resolve")
def resolve_source(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    """Where this period would read from right now, and whether it works."""
    src = source_tables.resolve(period)
    if src is None:
        return {"period": period, "source": None,
                "message": "No source table configured; this period uses uploaded data."}
    try:
        columns = source_tables.describe(src)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{src.label} could not be read: {exc}",
        ) from exc
    if not columns:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{src.label} does not exist. Check the dataset and table name.",
        )
    mapping, missing = source_tables.map_columns([c["column_name"] for c in columns])
    return {
        "period": period,
        "source": src.label,
        "origin": src.origin,
        "columns": len(columns),
        "mapped": mapping,
        "missing_required": missing,
        "usable": not missing,
    }


@router.post("/sources")
def set_source(
    body: SourceTableIn,
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    """Point a period at a different table.

    Refused for a locked month: the stored calculation would no longer match
    the data behind it. Reopen the month first.
    """
    month.require_open(body.period)

    candidate = source_tables.SourceTable(
        project=body.project or get_settings().gcp_project_id,
        dataset=body.dataset, table=body.table, date_column=body.date_column,
    )
    columns = source_tables.describe(candidate)
    if not columns:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{candidate.label} does not exist, or this service cannot read it.",
        )
    mapping, missing = source_tables.map_columns([c["column_name"] for c in columns])
    if missing and not body.column_map:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{candidate.label} has no column matching: {', '.join(missing)}. "
            f"Supply a column_map, or point at a table that has them.",
        )

    old = source_tables.resolve(body.period)
    src = source_tables.set_for_period(
        body.period, body.dataset, body.table, principal.email,
        body.project, body.date_column, body.column_map or mapping,
    )
    audit.record(
        principal.email, "SOURCE_TABLE_CHANGE", entity_type="period",
        affected_record=body.period,
        old_value={"source": old.label if old else None},
        new_value={"source": src.label}, reason=body.reason,
    )
    return {"period": body.period, "source": src.label,
            "columns_mapped": len(body.column_map or mapping)}


# --- calculation and month lifecycle --------------------------------------
@router.post("/incentive/calculate")
def recalculate(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    reason: str = Query(..., min_length=3),
    principal: Principal = Depends(require(Permission.RECALCULATE)),
):
    month.require_open(period)
    breakdowns = incentive_run.run(period, principal.email)
    total = sum(b.total_incentive for b in breakdowns.values())
    payable = sum(b.net_payable for b in breakdowns.values())
    version = next(iter(breakdowns.values())).calculation_version if breakdowns else 0
    audit.record(
        principal.email, "INCENTIVE_RECALCULATE", entity_type="period",
        affected_record=period,
        new_value={"employees": len(breakdowns), "total_incentive": total,
                   "version": version},
        reason=reason,
    )
    return {
        "period": period,
        "calculation_version": version,
        "employees": len(breakdowns),
        "total_incentive": total,
        "net_payable": payable,
        "accumulation": total - payable,
    }


@router.get("/period/{period}/status")
def period_status(period: str, principal: Principal = Depends(current_principal)):
    return {"period": period, "status": month.get_status(period).value}


@router.post("/period/{period}/status")
def set_period_status(
    period: str,
    to: MonthStatus = Query(...),
    reason: str = Query(..., min_length=3),
    principal: Principal = Depends(current_principal),
):
    needed = (
        Permission.REOPEN_MONTH
        if month.get_status(period) is MonthStatus.LOCKED
        else Permission.LOCK_MONTH
    )
    if not principal.can(needed):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Your role ({principal.role.value}) cannot change a "
            f"{month.get_status(period).value} month.",
        )
    new = month.transition(period, to, principal.email, reason)
    audit.record(
        principal.email,
        "MONTH_REOPEN" if to is MonthStatus.OPEN else "MONTH_STATUS_CHANGE",
        entity_type="period", affected_record=period,
        new_value={"status": new.value}, reason=reason,
    )
    return {"period": period, "status": new.value}


@router.get("/audit")
def read_audit(
    action: str | None = None,
    user_email: str | None = None,
    limit: int = Query(200, le=1000),
    principal: Principal = Depends(require(Permission.VIEW_AUDIT)),
):
    return audit.search(action, user_email, limit)
