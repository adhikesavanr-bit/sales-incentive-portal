"""Coupon master access."""
from __future__ import annotations

from app.config import get_settings
from app.db import bigquery as bq
from app.engines.qualification import (
    ClubbingOverride,
    QualificationOverride,
    min_sales_for,
)
from app.models.schemas import CouponSignature


def load_for_period(period: str) -> list[CouponSignature]:
    """The period's coupon signatures: those activated during the month.

    Each month's coupon consumption report, and so each month's Coupon
    Working, holds only coupons activated that month, and the workbook matches
    a sale to a coupon by code within that set. Reading earlier months'
    coupons as well would let a code reused across months pull a sale onto
    last month's signature: with July loaded, 13 August sales moved to a July
    SA151MMZ coupon and August's qualified revenue fell by Rs 1.67 lakh.

    Loaded wholesale (~1,200 rows/month) because clubbing needs the full set:
    a coupon's verdict depends on its siblings.
    """
    s = get_settings()
    rows = bq.query(
        f"SELECT * FROM {s.table('coupon_master')} "
        "WHERE activation_date IS NULL "
        "   OR DATE_TRUNC(activation_date, MONTH) = PARSE_DATE('%Y-%m', @p)",
        {"p": period},
    )
    return [
        CouponSignature(
            coupon_signature=r["coupon_signature"],
            coupon_code=r["coupon_code"],
            employee_id=r["employee_id"],
            initial=r.get("initial"),
            region=r.get("region"),
            college_id=r.get("college_id"),
            discount_group=r.get("discount_group"),
            group_size=r.get("group_size") or "G10",
            required_sales=int(r.get("required_sales") or 10),
            min_sales=int(r["min_sales"]) if r.get("min_sales") else
            min_sales_for(r.get("group_size") or "G10", int(r.get("required_sales") or 10)),
            activation_date=r.get("activation_date"),
            deactivation_date=r.get("deactivation_date"),
        )
        for r in rows
    ]


def load_overrides(period: str) -> tuple[list[QualificationOverride], list[ClubbingOverride]]:
    s = get_settings()
    q = bq.query(
        f"SELECT * FROM {s.table('qualification_override')} WHERE period = @p",
        {"p": period},
    )
    c = bq.query(
        f"SELECT * FROM {s.table('coupon_clubbing_override')} WHERE period = @p",
        {"p": period},
    )
    return (
        [QualificationOverride(
            coupon_signature=r["coupon_signature"],
            qualified=bool(r["qualified"]),
            reason=r["reason"],
            approved_by=r["approved_by"],
        ) for r in q],
        [ClubbingOverride(
            coupon_signatures=list(r.get("coupon_signatures") or []),
            group_label=r["group_label"],
            reason=r["reason"],
            approved_by=r["approved_by"],
        ) for r in c],
    )
