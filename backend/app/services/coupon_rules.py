"""Coupon qualification rules, versioned by effective date.

The point of this module is that changing a rule today must not change what a
previous month was paid. A calculation for 2026-08 reads the rows that were in
force on 2026-08-01; editing a threshold now writes a NEW row effective from
the change date and closes the old one, leaving history intact.

A locked month is doubly protected: its numbers are already stored in
`monthly_incentive` and are not recomputed unless someone reopens it, and even
then the rules it reads are the ones that applied at the time.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from app.config import get_settings
from app.db import bigquery as bq
from app.engines.qualification import (
    DEFAULT_MIN_OWN_SALES,
    MIN_OWN_SALES_BY_GROUP_SIZE,
    MIN_SALES_BY_GROUP_SIZE,
    REQUIRED_SALES_BY_GROUP_SIZE,
    CouponPolicy,
)
from app.models.schemas import CouponRule


def period_start(period: str) -> date:
    y, m = period.split("-")
    return date(int(y), int(m), 1)


def seed_rules() -> list[CouponRule]:
    """The values the approved August 2026 workbook used."""
    return [
        CouponRule(
            group_size=size,
            required_sales=REQUIRED_SALES_BY_GROUP_SIZE[size],
            min_sales=MIN_SALES_BY_GROUP_SIZE[size],
            min_own_sales=MIN_OWN_SALES_BY_GROUP_SIZE.get(size, DEFAULT_MIN_OWN_SALES),
            reason="Seeded from the approved Aug 2026 incentive workbook",
            updated_by="seed",
        )
        for size in ("G3", "G5", "G7", "G10", "G15", "G20", "G25")
    ]


def for_period(period: str) -> list[CouponRule]:
    """The rules in force on the first day of `period`.

    Falls back to the seed values when the table is empty, so a fresh install
    calculates identically to the workbook before anyone touches the screen.
    """
    s = get_settings()
    start = period_start(period).isoformat()
    try:
        rows = bq.query(
            f"""
            SELECT group_size, required_sales, min_sales, min_own_sales,
                   effective_from, effective_to, reason, updated_by
            FROM {s.table('coupon_rules')}
            WHERE effective_from <= DATE(@start)
              AND (effective_to IS NULL OR effective_to > DATE(@start))
            QUALIFY ROW_NUMBER() OVER (
              PARTITION BY group_size ORDER BY effective_from DESC
            ) = 1
            ORDER BY required_sales
            """,
            {"start": start},
        )
    except Exception:
        rows = []

    if not rows:
        return seed_rules()
    return [CouponRule(**r) for r in rows]


def policy_for_period(period: str) -> CouponPolicy:
    """The engine's view of the rules for a period."""
    rules = for_period(period)
    return CouponPolicy(
        min_sales={r.group_size: r.min_sales for r in rules},
        min_own_sales={r.group_size: r.min_own_sales for r in rules},
        default_min_own_sales=DEFAULT_MIN_OWN_SALES,
    )


def history(group_size: str | None = None) -> list[dict]:
    """Every version ever written, newest first. This is the audit view."""
    s = get_settings()
    where = "WHERE group_size = @g" if group_size else ""
    return bq.query(
        f"SELECT * FROM {s.table('coupon_rules')} {where} "
        "ORDER BY group_size, effective_from DESC",
        {"g": group_size} if group_size else {},
    )


def update(
    group_size: str,
    required_sales: int,
    min_sales: int,
    min_own_sales: int,
    effective_from: date,
    reason: str,
    updated_by: str,
) -> CouponRule:
    """Write a new version of one group size's rule.

    Closes the current row at `effective_from` rather than overwriting it, so
    the rule that applied to any past month remains readable.
    """
    s = get_settings()
    now = datetime.now(timezone.utc).isoformat()
    eff = effective_from.isoformat()

    # Close whatever is currently open for this size, from the same date.
    bq.query(
        f"UPDATE {s.table('coupon_rules')} SET effective_to = DATE(@eff) "
        "WHERE group_size = @g AND effective_to IS NULL "
        "  AND effective_from < DATE(@eff)",
        {"g": group_size, "eff": eff},
    )
    # A same-day re-edit replaces rather than stacks.
    bq.query(
        f"DELETE FROM {s.table('coupon_rules')} "
        "WHERE group_size = @g AND effective_from = DATE(@eff)",
        {"g": group_size, "eff": eff},
    )

    bq.insert_rows("coupon_rules", [{
        "rule_id": str(uuid.uuid4()),
        "group_size": group_size,
        "required_sales": required_sales,
        "min_sales": min_sales,
        "min_own_sales": min_own_sales,
        "effective_from": eff,
        "effective_to": None,
        "reason": reason,
        "updated_by": updated_by,
        "updated_at": now,
    }])
    return CouponRule(
        group_size=group_size,
        required_sales=required_sales,
        min_sales=min_sales,
        min_own_sales=min_own_sales,
        effective_from=effective_from,
        reason=reason,
        updated_by=updated_by,
    )


def ensure_seeded(updated_by: str = "seed") -> int:
    """Write the seed rules if the table is empty. Safe to call repeatedly."""
    s = get_settings()
    rows = bq.query(f"SELECT COUNT(*) AS n FROM {s.table('coupon_rules')}")
    if rows and int(rows[0]["n"]):
        return 0

    now = datetime.now(timezone.utc).isoformat()
    # Effective from well before any period the app will calculate, so the
    # first month read does not fall through to the code defaults.
    start = date(2020, 1, 1).isoformat()
    payload = [{
        "rule_id": str(uuid.uuid4()),
        "group_size": r.group_size,
        "required_sales": r.required_sales,
        "min_sales": r.min_sales,
        "min_own_sales": r.min_own_sales,
        "effective_from": start,
        "effective_to": None,
        "reason": r.reason,
        "updated_by": updated_by,
        "updated_at": now,
    } for r in seed_rules()]
    bq.insert_rows("coupon_rules", payload)
    return len(payload)
