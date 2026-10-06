"""Monthly targets: the scoped list, one-off edits and bulk uploads.

Every write appends a new version rather than editing in place, so the audit
trail and the target a month was paid against both survive.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import pandas as pd

from app.auth.rbac import Permission, Principal, visible_employee_sql
from app.config import get_settings
from app.db import bigquery as bq
from app.services import audit
from app.services.upload import read_file

# Winner units are Base x 1.3 in every month of the supplied workbook.
WINNER_MULTIPLIER = 1.3
MAX_BULK_ROWS = 5000

# Header spellings accepted in a bulk file, after lower-casing and turning
# spaces, dots and dashes into underscores.
_ID_COLUMNS = ("employee_id", "emp_id", "employee_code", "emp_code", "id")
_TARGET_COLUMNS = ("target_units", "base_units", "target", "base_target", "units")
_WINNER_COLUMNS = ("winner_units", "winner", "winner_target")


def in_scope(principal: Principal, period: str) -> list[dict]:
    """Everyone the principal may see, with their latest target for the period.

    LEFT JOIN from people to targets, so someone with no target for the period
    still appears with a blank row to fill in.
    """
    s = get_settings()
    scope, params = visible_employee_sql(principal)
    return bq.query(
        f"""
        SELECT h.employee_id, h.full_name, h.region, h.zone, h.designation,
               t.period, t.target_units, t.winner_units, t.status, t.version
        FROM {s.table('hierarchy_asof')}(@p) h
        LEFT JOIN (
          SELECT * FROM {s.table('targets')} WHERE period = @p
          QUALIFY ROW_NUMBER() OVER (PARTITION BY employee_id ORDER BY version DESC) = 1
        ) t ON t.employee_id = h.employee_id
        WHERE h.is_active AND {scope}
        ORDER BY h.region, h.full_name
        """,
        {**params, "p": period},
    )


def version_row(principal: Principal, employee_id: str, period: str, units: float,
         winner: float | None, version: int, now: str) -> dict:
    approver = principal.can(Permission.APPROVE_TARGETS)
    return {
        "employee_id": employee_id,
        "period": period,
        "vertical": "Marrow",
        "target_units": units,
        "winner_units": winner if winner is not None else units * WINNER_MULTIPLIER,
        "status": "APPROVED" if approver else "SUBMITTED",
        "submitted_by": principal.email,
        "approved_by": principal.email if approver else None,
        "approved_at": now if approver else None,
        "version": version,
        "updated_at": now,
    }


# --- bulk upload -----------------------------------------------------------
def _norm(name: object) -> str:
    return re.sub(r"[\s.\-/]+", "_", str(name).strip().lower()).strip("_")


def _pick(columns: dict[str, str], options: tuple[str, ...]) -> str | None:
    return next((columns[o] for o in options if o in columns), None)


def _number(value) -> float | None:
    """A cell as a number, or None when it is blank."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    return float(text)  # ValueError for anything else, reported per row


def parse(filename: str, content: bytes) -> list[dict]:
    """The file's rows as {line, employee_id, target, winner} with raw values."""
    df = read_file(filename, content)
    df = df.dropna(how="all")
    columns = {_norm(c): c for c in df.columns}
    id_col = _pick(columns, _ID_COLUMNS)
    target_col = _pick(columns, _TARGET_COLUMNS)
    if id_col is None or target_col is None:
        raise ValueError(
            "The file needs an 'employee_id' column and a 'target_units' column. "
            "Download the template to get the right headings."
        )
    if len(df) > MAX_BULK_ROWS:
        raise ValueError(f"At most {MAX_BULK_ROWS} rows per file.")
    winner_col = _pick(columns, _WINNER_COLUMNS)
    out = []
    for i, (_, r) in enumerate(df.iterrows()):
        raw_id = r[id_col]
        out.append({
            "line": i + 2,  # row 1 is the header
            "employee_id": "" if pd.isna(raw_id) else str(raw_id).strip(),
            "target": r[target_col],
            "winner": r[winner_col] if winner_col else None,
        })
    return out


def preview(principal: Principal, period: str, rows: list[dict]) -> dict:
    """Check every row against the caller's scope and current targets. Writes nothing."""
    people = {p["employee_id"]: p for p in in_scope(principal, period)}
    seen: set[str] = set()
    checked = []
    for r in rows:
        emp = r["employee_id"]
        person = people.get(emp)
        item = {
            "line": r["line"],
            "employee_id": emp,
            "full_name": person["full_name"] if person else None,
            "region": person["region"] if person else None,
            "old_target_units": person["target_units"] if person else None,
            "target_units": None,
            "winner_units": None,
            "status": "error",
            "error": None,
        }
        try:
            units = _number(r["target"])
            winner = _number(r["winner"])
        except ValueError:
            item["error"] = "Target must be a number."
            checked.append(item)
            continue
        if not emp:
            item["error"] = "Employee ID is blank."
        elif person is None:
            # Same wording for unknown and out-of-scope: no org-chart leaks.
            item["error"] = "Not an active employee in your reporting line."
        elif emp in seen:
            item["error"] = "Listed more than once in this file."
        elif units is None:
            item["status"] = "skipped"
        elif units < 0 or (winner is not None and winner < 0):
            item["error"] = "Targets cannot be negative."
        else:
            item["target_units"] = units
            item["winner_units"] = winner if winner is not None else units * WINNER_MULTIPLIER
            old = person["target_units"]
            same_winner = winner is None or winner == person["winner_units"]
            unchanged = old is not None and float(old) == units and same_winner
            item["status"] = "unchanged" if unchanged else "change"
        seen.add(emp)
        checked.append(item)

    counts = {k: sum(1 for c in checked if c["status"] == k)
              for k in ("change", "unchanged", "skipped", "error")}
    return {"period": period, "rows": checked, "counts": counts}


def commit(principal: Principal, period: str, checked: dict, reason: str) -> int:
    """Write every changed row in one load job, and audit each one."""
    changes = [c for c in checked["rows"] if c["status"] == "change"]
    if not changes:
        return 0
    s = get_settings()
    ids = [c["employee_id"] for c in changes]
    versions = {
        r["employee_id"]: int(r["v"])
        for r in bq.query(
            f"SELECT employee_id, MAX(version) AS v FROM {s.table('targets')} "
            "WHERE period = @p AND employee_id IN UNNEST(@ids) GROUP BY employee_id",
            {"p": period, "ids": ids},
        )
    }
    now = datetime.now(timezone.utc).isoformat()
    bq.append_rows("targets", [
        version_row(principal, c["employee_id"], period, c["target_units"], c["winner_units"],
             versions.get(c["employee_id"], 0) + 1, now)
        for c in changes
    ])
    audit.record_many(principal.email, "TARGET_UPDATE", [
        {
            "entity_type": "target",
            "affected_record": f"{c['employee_id']}:{period}",
            "old_value": {"target_units": c["old_target_units"]},
            "new_value": {"target_units": c["target_units"],
                          "winner_units": c["winner_units"], "via": "bulk upload"},
        }
        for c in changes
    ], reason=reason)
    return len(changes)
