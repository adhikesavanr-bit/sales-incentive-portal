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


def list_all(active_only: bool = True, period: str | None = None) -> list[Employee]:
    """Everyone today, or as the hierarchy stood in `period` ('YYYY-MM')."""
    s = get_settings()
    where = "WHERE is_active" if active_only else ""
    if period:
        rows = bq.query(f"SELECT * FROM {s.table('hierarchy_asof')}(@p) {where}", {"p": period})
    else:
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


def is_in_scope(employee_id: str, scope_sql: str, params: dict,
                period: str | None = None) -> bool:
    """The single check that stops `?employee_id=NHP002` from working.

    With a period, the check uses the hierarchy of that month: a manager can
    open the July page of someone who was in their team in July.
    """
    s = get_settings()
    source = (f"{s.table('hierarchy_asof')}(@scope_period)" if period
              else s.table('v_employee_hierarchy'))
    extra = {"scope_period": period} if period else {}
    rows = bq.query(
        f"SELECT 1 FROM {source} h "
        f"WHERE h.employee_id = @target AND ({scope_sql}) LIMIT 1",
        {**params, **extra, "target": employee_id},
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


def latest_effective_from(employee_id: str) -> date | None:
    """The date the person's current record took effect, if they have one."""
    s = get_settings()
    rows = bq.query(
        f"SELECT MAX(effective_from) AS d FROM {s.table('employee_master')} "
        "WHERE employee_id = @id",
        {"id": employee_id},
    )
    return rows[0]["d"] if rows else None


def upsert(employee: Employee, updated_by: str, effective_from: date | None = None) -> None:
    """Close the current row and open a new one (SCD-2).

    `effective_from` is the first day the change applies (the month it was
    chosen for in People); months before it keep the previous row. Defaults
    to today.
    """
    s = get_settings()
    now = datetime.now(timezone.utc)
    eff = effective_from or now.date()
    bq.query(
        f"UPDATE {s.table('employee_master')} "
        "SET effective_to = @eff, updated_at = CURRENT_TIMESTAMP() "
        "WHERE employee_id = @id AND effective_to IS NULL",
        {"id": employee.employee_id, "eff": eff},
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
        "effective_from": eff.isoformat(),
        "effective_to": None,
        "updated_at": now.isoformat(),
        "updated_by": updated_by,
    }])
    forget_lookups()


def set_hierarchy(employee: Employee, updated_by: str,
                  effective_from: date | None = None) -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    eff = effective_from or now.date()
    bq.query(
        f"UPDATE {s.table('reporting_hierarchy')} "
        "SET effective_to = @eff, updated_at = CURRENT_TIMESTAMP() "
        "WHERE employee_id = @id AND effective_to IS NULL",
        {"id": employee.employee_id, "eff": eff},
    )
    bq.append_rows("reporting_hierarchy", [{
        "employee_id": employee.employee_id,
        "submanager_id": employee.submanager_id,
        "rm_id": employee.rm_id,
        "zm_id": employee.zm_id,
        "business_head_id": employee.business_head_id,
        "region": employee.region,
        "zone": employee.zone,
        "effective_from": eff.isoformat(),
        "effective_to": None,
        "updated_at": now.isoformat(),
        "updated_by": updated_by,
    }])
    forget_lookups()


def region_directory(people: list[Employee]) -> dict:
    """Regions and zones as they stand today, with who leads each.

    Feeds the People form's dropdowns, so picking a region also fills in its
    zone, RM and zonal manager. Without it, changing a region by hand left the
    old RM and zonal manager in place (Sep 2026: NHP842 and NHP536 moved region
    but still sat in their old RM's team).

      region -> zone:  the zone most of its people are in
      region -> RM:    the active Regional Manager placed in that region, or
                       failing that the RM most of its people report to
      region/zone -> zonal manager: the one most of its people have
    """
    from collections import Counter

    active = [p for p in people if p.is_active]
    names = {p.employee_id: p.full_name for p in people}

    def _top(values) -> str | None:
        counts = Counter(v for v in values if v)
        return counts.most_common(1)[0][0] if counts else None

    regions = []
    for region in sorted({p.region for p in active if p.region}, key=_region_sort_key):
        members = [p for p in active if p.region == region]
        rms = [p.employee_id for p in members if p.role is Role.REGIONAL_MANAGER]
        rm = rms[0] if len(rms) == 1 else _top(p.rm_id for p in members)
        zm = _top(p.zm_id for p in members)
        regions.append({
            "region": region,
            "zone": _top(p.zone for p in members),
            "rm_id": rm, "rm_name": names.get(rm),
            "zm_id": zm, "zm_name": names.get(zm),
            "people": len(members),
        })

    zones = []
    for zone in sorted({p.zone for p in active if p.zone}):
        members = [p for p in active if p.zone == zone]
        zm = _top(p.zm_id for p in members)
        zones.append({"zone": zone, "zm_id": zm, "zm_name": names.get(zm),
                      "people": len(members)})
    return {"regions": regions, "zones": zones}


def _region_sort_key(region: str):
    """R2 before R10: order by the number after the R, then by name."""
    import re
    m = re.match(r"R(\d+)", region or "")
    return (int(m.group(1)) if m else 10_000, region)
