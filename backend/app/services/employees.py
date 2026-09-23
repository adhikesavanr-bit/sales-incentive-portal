"""Employee master and reporting hierarchy."""
from __future__ import annotations

from datetime import datetime, timezone

from app.config import get_settings
from app.db import bigquery as bq
from app.models.schemas import Employee, Role


def _row_to_employee(r: dict) -> Employee:
    return Employee(
        employee_id=r["employee_id"],
        full_name=r["full_name"],
        initial=r.get("initial"),
        email=r.get("email"),
        role=Role(r.get("role") or "BDE"),
        designation=r.get("designation"),
        region=r.get("region"),
        zone=r.get("zone"),
        vertical=r.get("vertical") or "Marrow",
        submanager_id=r.get("submanager_id"),
        rm_id=r.get("rm_id"),
        zm_id=r.get("zm_id"),
        business_head_id=r.get("business_head_id"),
        is_active=bool(r.get("is_active", True)),
    )


def get_by_email(email: str) -> Employee | None:
    s = get_settings()
    rows = bq.query(
        f"SELECT * FROM {s.table('v_employee_hierarchy')} "
        "WHERE LOWER(email) = @email LIMIT 1",
        {"email": email.lower()},
    )
    return _row_to_employee(rows[0]) if rows else None


def get_by_id(employee_id: str) -> Employee | None:
    s = get_settings()
    rows = bq.query(
        f"SELECT * FROM {s.table('v_employee_hierarchy')} "
        "WHERE employee_id = @id LIMIT 1",
        {"id": employee_id},
    )
    return _row_to_employee(rows[0]) if rows else None


def list_all(active_only: bool = True) -> list[Employee]:
    s = get_settings()
    where = "WHERE is_active" if active_only else ""
    rows = bq.query(f"SELECT * FROM {s.table('v_employee_hierarchy')} {where}")
    return [_row_to_employee(r) for r in rows]


def list_in_scope(scope_sql: str, params: dict) -> list[Employee]:
    """Employees visible to a principal. `scope_sql` comes from rbac."""
    s = get_settings()
    rows = bq.query(
        f"SELECT h.* FROM {s.table('v_employee_hierarchy')} h WHERE {scope_sql}",
        params,
    )
    return [_row_to_employee(r) for r in rows]


def is_in_scope(employee_id: str, scope_sql: str, params: dict) -> bool:
    """The single check that stops `?employee_id=NHP002` from working."""
    s = get_settings()
    rows = bq.query(
        f"SELECT 1 FROM {s.table('v_employee_hierarchy')} h "
        f"WHERE h.employee_id = @target AND ({scope_sql}) LIMIT 1",
        {**params, "target": employee_id},
    )
    return bool(rows)


def upsert(employee: Employee, updated_by: str) -> None:
    """Close the current row and open a new one (SCD-2)."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    bq.query(
        f"UPDATE {s.table('employee_master')} "
        "SET effective_to = CURRENT_DATE(), updated_at = CURRENT_TIMESTAMP() "
        "WHERE employee_id = @id AND effective_to IS NULL",
        {"id": employee.employee_id},
    )
    bq.insert_rows("employee_master", [{
        "employee_id": employee.employee_id,
        "full_name": employee.full_name,
        "initial": employee.initial,
        "email": employee.email,
        "role": employee.role.value,
        "designation": employee.designation,
        "region": employee.region,
        "zone": employee.zone,
        "vertical": employee.vertical,
        "is_active": employee.is_active,
        "effective_from": now.date().isoformat(),
        "effective_to": None,
        "updated_at": now.isoformat(),
        "updated_by": updated_by,
    }])


def set_hierarchy(employee: Employee, updated_by: str) -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    bq.query(
        f"UPDATE {s.table('reporting_hierarchy')} "
        "SET effective_to = CURRENT_DATE(), updated_at = CURRENT_TIMESTAMP() "
        "WHERE employee_id = @id AND effective_to IS NULL",
        {"id": employee.employee_id},
    )
    bq.insert_rows("reporting_hierarchy", [{
        "employee_id": employee.employee_id,
        "submanager_id": employee.submanager_id,
        "rm_id": employee.rm_id,
        "zm_id": employee.zm_id,
        "business_head_id": employee.business_head_id,
        "region": employee.region,
        "zone": employee.zone,
        "effective_from": now.date().isoformat(),
        "effective_to": None,
        "updated_at": now.isoformat(),
        "updated_by": updated_by,
    }])
