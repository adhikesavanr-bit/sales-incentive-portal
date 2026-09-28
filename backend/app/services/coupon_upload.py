"""The month's coupons, straight from the coupon consumption report.

This replaces building the Coupon Working sheet by hand. Checked against the
July 2026 working: the same 686 of 836 coupons, and the same owner, zone,
group size, required and minimum sales, dates and college for every one.

  * A coupon is a field coupon when its `sales_master` code is in the coupon
    agent list. Every other code is left out, as the working leaves it out.
  * Owner and zone come from the agent list.
  * Group size is the `G<n>` in the discount group id: 1_5_FIELDG10 -> G10.
    The misspelt 7_5_FILEDG15 is still G15.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from app.config import get_settings
from app.db import bigquery as bq
from app.engines.qualification import (
    MIN_SALES_BY_GROUP_SIZE,
    REQUIRED_SALES_BY_GROUP_SIZE,
    min_sales_for,
)
from app.services import audit
from app.services.upload import read_file

REPORT_COLUMNS = ("coupon_signature", "coupon", "activation_date", "deactivation_date",
                  "discount_group_id", "sales_master", "college_ids")
_GROUP_SIZE = re.compile(r"G(\d+)", re.IGNORECASE)
# Marks this upload's rows, so uploading the month again replaces them.
_BATCH_PREFIX = "COUPON_REPORT"


def _norm(name: object) -> str:
    return re.sub(r"[\s.\-/]+", "_", str(name).strip().lower()).strip("_")


def _text(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    t = str(value).strip()
    return t or None


def _date(value) -> str | None:
    """'26/07/01 11:47' (yy/mm/dd, IST) as '2026-07-01'."""
    t = _text(value)
    if t is None:
        return None
    for fmt in ("%y/%m/%d %H:%M", "%y/%m/%d %H:%M:%S", "%y/%m/%d"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    parsed = pd.to_datetime(t, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"'{t}' is not a date")
    return parsed.date().isoformat()


# --- agent list --------------------------------------------------------------
_AGENT_COLUMNS = {
    "initial": ("bde_initial", "initial", "code", "sales_master"),
    "agent_name": ("bde_name", "agent_name", "name"),
    "employee_id": ("employee_ids", "employee_id", "emp_id"),
    "zone": ("zone", "region"),
}


def parse_agents(filename: str, content: bytes) -> list[dict]:
    """The Coupon_Agent Details sheet as rows. Raises ValueError when unusable."""
    df = read_file(filename, content).dropna(how="all")
    columns = {_norm(c): c for c in df.columns}
    pick = {}
    for field, options in _AGENT_COLUMNS.items():
        col = next((columns[o] for o in options if o in columns), None)
        if col is None and field in ("initial", "employee_id"):
            raise ValueError(
                "The agent list needs 'BDE Initial' and 'Employee IDs' columns, "
                "as in Coupon_Agent Details.xlsx."
            )
        pick[field] = col
    rows, seen = [], {}
    for i, (_, r) in enumerate(df.iterrows()):
        initial = _text(r[pick["initial"]])
        emp = _text(r[pick["employee_id"]])
        if not initial and not emp:
            continue
        if not initial or not emp:
            raise ValueError(f"Row {i + 2}: both the initial and the employee ID are needed.")
        initial = initial.upper()
        if initial in seen and seen[initial] != emp:
            raise ValueError(f"'{initial}' is listed for both {seen[initial]} and {emp}.")
        seen[initial] = emp
        rows.append({
            "initial": initial,
            "employee_id": emp,
            "agent_name": _text(r[pick["agent_name"]]) if pick["agent_name"] else None,
            "zone": _text(r[pick["zone"]]) if pick["zone"] else None,
        })
    if not rows:
        raise ValueError("The agent list is empty.")
    # One row per initial, last one wins (they agree, checked above).
    return list({r["initial"]: r for r in rows}.values())


def load_agents() -> dict[str, dict]:
    s = get_settings()
    return {r["initial"].upper(): r
            for r in bq.query(f"SELECT * FROM {s.table('coupon_agents')}")}


def agents_summary() -> dict:
    s = get_settings()
    r = bq.query(
        f"SELECT COUNT(*) AS n, MAX(updated_at) AS updated_at, "
        f"ANY_VALUE(updated_by HAVING MAX updated_at) AS updated_by "
        f"FROM {s.table('coupon_agents')}"
    )[0]
    return {"agents": int(r["n"]), "updated_at": r["updated_at"], "updated_by": r["updated_by"]}


def replace_agents(rows: list[dict], who: str, reason: str) -> int:
    s = get_settings()
    before = agents_summary()["agents"]
    now = datetime.now(timezone.utc).isoformat()
    bq.query(f"DELETE FROM {s.table('coupon_agents')} WHERE TRUE")
    bq.append_rows("coupon_agents", [{**r, "updated_at": now, "updated_by": who} for r in rows])
    audit.record(who, "COUPON_AGENTS_REPLACE", entity_type="coupon_agents",
                 old_value={"agents": before}, new_value={"agents": len(rows)}, reason=reason)
    return len(rows)


# --- consumption report ------------------------------------------------------
def parse_report(filename: str, content: bytes) -> pd.DataFrame:
    df = read_file(filename, content).dropna(how="all")
    df.columns = [_norm(c) for c in df.columns]
    missing = [c for c in REPORT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            "This does not look like a coupon consumption report: no "
            f"{', '.join(repr(m) for m in missing)} column. Upload the "
            "coupon_consumption_report_….csv for the month."
        )
    return df


def build(df: pd.DataFrame, agents: dict[str, dict], period: str) -> dict:
    """Coupon master rows for the report, and a summary of what was left out."""
    rows, errors, warnings = [], [], []
    excluded: Counter[str] = Counter()
    seen: set[str] = set()
    months: Counter[str] = Counter()
    odd_sizes: set[str] = set()

    for i, (_, r) in enumerate(df.iterrows()):
        line = i + 2
        sig = _text(r["coupon_signature"])
        code = (_text(r["sales_master"]) or "").upper()
        if not sig:
            errors.append({"line": line, "message": "coupon_signature is blank."})
            continue
        if sig in seen:
            errors.append({"line": line, "message": f"{sig} appears more than once."})
            continue
        seen.add(sig)
        agent = agents.get(code)
        if agent is None:
            excluded[code or "(blank)"] += 1
            continue
        group = _text(r["discount_group_id"]) or ""
        m = _GROUP_SIZE.search(group)
        if not m:
            errors.append({"line": line, "message": f"No group size in discount group '{group}'."})
            continue
        size = f"G{int(m.group(1))}"
        required = REQUIRED_SALES_BY_GROUP_SIZE.get(size, int(m.group(1)))
        if size not in MIN_SALES_BY_GROUP_SIZE:
            odd_sizes.add(size)
        try:
            activation = _date(r["activation_date"])
            deactivation = _date(r["deactivation_date"])
        except ValueError as exc:
            errors.append({"line": line, "message": f"Bad date: {exc}."})
            continue
        if activation:
            months[activation[:7]] += 1
        rows.append({
            "coupon_signature": sig,
            "coupon_code": _text(r["coupon"]),
            "employee_id": agent["employee_id"],
            "initial": code,
            "region": agent.get("zone"),
            "college_id": _text(r["college_ids"]),
            "discount_group": group,
            "group_size": size,
            "required_sales": required,
            "min_sales": min_sales_for(size, required),
            "activation_date": activation,
            "deactivation_date": deactivation,
        })

    if months and months.most_common(1)[0][0] != period:
        warnings.append(
            f"Most coupons in this file were activated in {months.most_common(1)[0][0]}, "
            f"not {period}. Check the month selected."
        )
    for size in sorted(odd_sizes):
        warnings.append(f"Group size {size} has no minimum-sales rule; its coupons use the default.")

    owners = Counter(r["employee_id"] for r in rows)
    return {
        "period": period,
        "total": len(df),
        "included": len(rows),
        "excluded": sum(excluded.values()),
        "excluded_by_code": dict(excluded.most_common()),
        "owners": len(owners),
        "by_group_size": dict(Counter(r["group_size"] for r in rows).most_common()),
        "errors": errors,
        "warnings": warnings,
        "rows": rows,
    }


def commit(built: dict, period: str, who: str, reason: str) -> int:
    """Replace this month's uploaded coupons with the file's.

    Removes the rows of any earlier upload for the month plus any signature in
    this file, so a corrected report can be uploaded again without duplicates.
    Coupons of other months, including those loaded from the workbooks, stay.
    """
    s = get_settings()
    rows = built["rows"]
    batch = f"{_BATCH_PREFIX}:{period}:{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    before = bq.query(
        f"SELECT COUNT(*) AS n FROM {s.table('coupon_master')} "
        "WHERE STARTS_WITH(upload_batch_id, @prefix)",
        {"prefix": f"{_BATCH_PREFIX}:{period}:"},
    )[0]["n"]
    bq.query(
        f"DELETE FROM {s.table('coupon_master')} "
        "WHERE STARTS_WITH(upload_batch_id, @prefix) OR coupon_signature IN UNNEST(@sigs)",
        {"prefix": f"{_BATCH_PREFIX}:{period}:", "sigs": [r["coupon_signature"] for r in rows]},
    )
    bq.append_rows("coupon_master", [
        {**r, "upload_batch_id": batch, "ingested_at": now} for r in rows
    ])
    audit.record(
        who, "COUPON_REPORT_IMPORT", entity_type="coupon_master", affected_record=period,
        old_value={"coupons_from_earlier_upload": int(before)},
        new_value={"coupons": len(rows), "excluded": built["excluded"], "batch": batch},
        reason=reason,
    )
    return len(rows)
