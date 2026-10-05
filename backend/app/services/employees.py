"""Employee master and reporting hierarchy."""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone

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
        exit_date=r.get("exit_date"),
    )


# Every request resolves its caller from the employee master, which cost a
# BigQuery round trip on each call. The record is kept for five minutes, the
# same as dashboard results, so a page opened after a pause does not wait on
# this lookup before its own queries. An edit made here clears it on this
# instance at once; other instances see a role change or deactivation within
# the TTL.
_LOOKUP_TTL_SECONDS = 300
_lookup_cache: dict[tuple[str, str], tuple[float, Employee]] = {}
_lookup_lock = threading.Lock()


def forget_lookups() -> None:
    with _lookup_lock:
        _lookup_cache.clear()


def _cached_lookup(key: tuple[str, str], sql_where: str, params: dict) -> Employee | None:
    with _lookup_lock:
        hit = _lookup_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1].model_copy()
    s = get_settings()
    rows = bq.query(
        f"SELECT * FROM {s.table('v_employee_hierarchy')} WHERE {sql_where} LIMIT 1",
        params,
    )
    if not rows:
        return None
    employee = _row_to_employee(rows[0])
    with _lookup_lock:
        _lookup_cache[key] = (time.monotonic() + _LOOKUP_TTL_SECONDS, employee)
    return employee.model_copy()


def get_by_email(email: str) -> Employee | None:
    email = email.lower()
    return _cached_lookup(("email", email), "LOWER(email) = @email", {"email": email})


def get_by_id(employee_id: str) -> Employee | None:
    return _cached_lookup(("id", employee_id), "employee_id = @id", {"id": employee_id})


def deactivate(employee_id: str, exit_date: date, updated_by: str) -> Employee:
    """Mark someone as left. The row stays; only `is_active` and `exit_date` change.

    Deleting would orphan their past sales and break every month they appear
    in, so leavers are closed rather than removed.
    """
    existing = get_by_id(employee_id)
    if existing is None:
        raise ValueError(f"No employee {employee_id}")
    existing.is_active = False
    existing.exit_date = exit_date
    upsert(existing, updated_by)
    return existing


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


def normalise_initial(employee: Employee) -> None:
    """Coupon initials are matched upper-case; a blank one means none."""
    code = (employee.initial or "").strip().upper()
    employee.initial = code or None


def initial_conflict(employee: Employee) -> str | None:
    """Why this person's coupon initial cannot be used, or None if it can.

    The initial decides who a coupon's sales belong to, so one code must
    never point at two people: not two people in People, and not a person
    and a different employee on the uploaded agent list.
    """
    if not employee.initial:
        return None
    s = get_settings()
    params = {"code": employee.initial, "id": employee.employee_id}
    people = bq.query(
        f"SELECT employee_id, full_name FROM {s.table('v_employee_hierarchy')} "
        "WHERE UPPER(initial) = @code AND employee_id != @id AND is_active LIMIT 1",
        params,
    )
    if people:
        p = people[0]
        return (f"Coupon initial {employee.initial} already belongs to "
                f"{p['full_name']} ({p['employee_id']}).")
    agents = bq.query(
        f"SELECT employee_id FROM {s.table('coupon_agents')} "
        "WHERE UPPER(initial) = @code AND employee_id != @id LIMIT 1",
        params,
    )
    if agents:
        return (f"Coupon initial {employee.initial} is on the coupon agent list for "
                f"{agents[0]['employee_id']}.")
    return None


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
    bq.append_rows("employee_master", [{
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
        "exit_date": employee.exit_date.isoformat() if employee.exit_date else None,
        "effective_from": now.date().isoformat(),
        "effective_to": None,
        "updated_at": now.isoformat(),
        "updated_by": updated_by,
    }])
    forget_lookups()


def set_hierarchy(employee: Employee, updated_by: str) -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    bq.query(
        f"UPDATE {s.table('reporting_hierarchy')} "
        "SET effective_to = CURRENT_DATE(), updated_at = CURRENT_TIMESTAMP() "
        "WHERE employee_id = @id AND effective_to IS NULL",
        {"id": employee.employee_id},
    )
    bq.append_rows("reporting_hierarchy", [{
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
    forget_lookups()
