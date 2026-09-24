"""Orchestrates a full period calculation and persists the result."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import get_settings
from app.db import bigquery as bq
from app.engines.incentive import (
    DEFAULT_SLAB_TABLES,
    IncentiveConfig,
    ManualAdjustment,
    Slab,
    SlabTable,
    calculate_period,
)
from app.engines.qualification import run_qualification
from app.models.schemas import (
    IncentiveBreakdown,
    SalesTransaction,
    SlabScope,
    Target,
)
from app.services import coupon_rules, coupons as coupon_service
from app.services import employees as employee_service
from app.services import source_tables


def load_slab_tables(period: str) -> dict[SlabScope, SlabTable]:
    """Read slabs from `incentive_rules`, falling back to the seeded Policy values."""
    s = get_settings()
    rows = bq.query(
        f"""
        SELECT scope, threshold, rate, source_note
        FROM {s.table('incentive_rules')}
        WHERE effective_from <= LAST_DAY(PARSE_DATE('%Y-%m', @p))
          AND (effective_to IS NULL
               OR effective_to >= PARSE_DATE('%Y-%m', @p))
        ORDER BY scope, threshold
        """,
        {"p": period},
    )
    if not rows:
        return dict(DEFAULT_SLAB_TABLES)

    grouped: dict[SlabScope, list[Slab]] = {}
    notes: dict[SlabScope, str | None] = {}
    for r in rows:
        try:
            scope = SlabScope(r["scope"])
        except ValueError:
            continue
        grouped.setdefault(scope, []).append(
            Slab(float(r["threshold"]), float(r["rate"]))
        )
        notes[scope] = r.get("source_note")

    tables = dict(DEFAULT_SLAB_TABLES)
    for scope, slabs in grouped.items():
        tables[scope] = SlabTable(scope=scope, slabs=slabs, source_note=notes.get(scope))
    return tables


def load_transactions(period: str) -> list[SalesTransaction]:
    """Read the period's sales.

    A configured source table wins; otherwise fall back to whatever was
    uploaded into raw_sales, so both workflows keep working.
    """
    src = source_tables.resolve(period)
    if src is not None:
        return source_tables.load_transactions(period, src)

    s = get_settings()
    rows = bq.query(
        f"""
        SELECT payment_id, order_id, invoice_id, payment_status, payment_date_ist,
               paid_amount, net_amount, plan_id, plan_title, plan_duration_in_month,
               course_id, user_id, college_id, college_name, coupon, coupon_agent, state
        FROM {s.table('raw_sales')}
        WHERE FORMAT_TIMESTAMP('%Y-%m', payment_date_ist, 'Asia/Kolkata') = @p
        """,
        {"p": period},
    )
    return [
        SalesTransaction(
            payment_id=r["payment_id"],
            order_id=r.get("order_id"),
            invoice_id=r.get("invoice_id"),
            payment_status=r["payment_status"],
            payment_date_ist=r["payment_date_ist"],
            paid_amount=float(r["paid_amount"]),
            net_amount=float(r["net_amount"]),
            plan_id=r.get("plan_id"),
            plan_title=r.get("plan_title"),
            plan_duration_in_month=r.get("plan_duration_in_month"),
            course_id=r.get("course_id"),
            user_id=r.get("user_id"),
            college_id=r.get("college_id"),
            college_name=r.get("college_name"),
            coupon=r.get("coupon"),
            coupon_agent=r.get("coupon_agent"),
            state=r.get("state"),
        )
        for r in rows
    ]


def load_targets(period: str) -> dict[str, Target]:
    s = get_settings()
    rows = bq.query(
        f"SELECT * FROM {s.table('targets')} "
        "WHERE period = @p AND status = 'APPROVED'",
        {"p": period},
    )
    return {
        r["employee_id"]: Target(
            employee_id=r["employee_id"],
            period=period,
            target_units=float(r["target_units"]),
            winner_units=float(r.get("winner_units") or 0),
            status=r["status"],
            approved_by=r.get("approved_by"),
        )
        for r in rows
    }


def load_adjustments(period: str) -> dict[str, ManualAdjustment]:
    s = get_settings()
    rows = bq.query(
        f"SELECT * FROM {s.table('manual_adjustment')} WHERE period = @p", {"p": period}
    )
    return {
        r["employee_id"]: ManualAdjustment(
            employee_id=r["employee_id"],
            period=period,
            incremental_pct=float(r.get("incremental_pct") or 0),
            other_incentive=float(r.get("other_incentive") or 0),
            prior_period_adjustment=float(r.get("prior_period_adjustment") or 0),
            reason=r.get("reason") or "",
            entered_by=r.get("entered_by") or "",
        )
        for r in rows
    }


def next_version(period: str) -> int:
    s = get_settings()
    rows = bq.query(
        f"SELECT IFNULL(MAX(calculation_version), 0) AS v "
        f"FROM {s.table('monthly_incentive')} WHERE period = @p",
        {"p": period},
    )
    return int(rows[0]["v"]) + 1 if rows else 1


def run(period: str, calculated_by: str) -> dict[str, IncentiveBreakdown]:
    """Full recalculation for a period. Writes a new calculation version."""
    s = get_settings()
    cfg = IncentiveConfig(
        arpu=s.default_arpu,
        arpu_threshold=s.default_arpu,
        monthly_cap=s.monthly_payout_cap,
        treat_zero_sales_as_exit=s.treat_zero_sales_as_exit,
        slab_tables=load_slab_tables(period),
    )

    transactions = load_transactions(period)
    signatures = coupon_service.load_for_period(period)
    q_over, c_over = coupon_service.load_overrides(period)

    qres = run_qualification(
        transactions,
        signatures,
        qualification_overrides=q_over,
        clubbing_overrides=c_over,
        non_field_regions=s.non_field_regions,
        # The rules as they stood at the start of this period, not as they
        # stand today. Recalculating August next year gives August's answer.
        policy=coupon_rules.policy_for_period(period),
    )

    employees = {e.employee_id: e for e in employee_service.list_all(active_only=False)}
    targets = load_targets(period)
    adjustments = load_adjustments(period)
    version = next_version(period)

    breakdowns = calculate_period(
        qres.transactions, period, employees, targets, adjustments, cfg, version
    )
    src = source_tables.resolve(period)
    source_label = src.label if src else "uploaded raw_sales"

    now = datetime.now(timezone.utc).isoformat()
    bq.load_rows("signature_qualification", [
        {**sq.model_dump(), "period": period, "calculated_at": now}
        for sq in qres.signatures.values()
    ])
    bq.load_rows("transaction_qualification", [
        {
            "period": period,
            "payment_id": t.payment_id,
            "employee_id": t.employee_id,
            "coupon_signature": t.coupon_signature,
            "status": t.status.value,
            "disqualification_reason": t.reason.value if t.reason else None,
            "reason_detail": t.reason_detail,
            "is_foundation": t.is_foundation,
            "coupon_region": t.coupon_region,
            "is_field": t.is_field,
            "paid_amount": t.paid_amount,
            "net_amount": t.net_amount,
            "calculation_version": version,
            "calculated_at": now,
        }
        for t in qres.transactions
    ])
    bq.load_rows("monthly_incentive", [
        {
            **b.model_dump(exclude={"slab_hits", "calculated_at", "arpu_threshold"}),
            # JSON column: serialise, as with column_map above.
            "slab_hits": json.dumps([h.model_dump() for h in b.slab_hits]),
            "calculated_at": now,
            "calculated_by": calculated_by,
            "notes": list(b.notes) + [f"Source: {source_label}"],
        }
        for b in breakdowns.values()
    ])
    return breakdowns
