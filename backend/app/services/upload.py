"""Sales upload: accept -> detect -> validate -> qualify -> preview -> import.

Nothing touches `raw_sales` until the admin explicitly confirms the import.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pandas as pd

from app.config import get_settings
from app.db import bigquery as bq
from app.engines.qualification import run_qualification
from app.models.schemas import (
    SalesTransaction,
    UploadSummary,
    ValidationError,
)
from app.services import coupons as coupon_service

# Source column -> BigQuery column. Left side is what the extract actually
# ships; the `paid_amount_*_100/118` name is not a legal BQ identifier.
COLUMN_MAP = {
    "order_id": "order_id",
    "invoice_id": "invoice_id",
    "payment_id": "payment_id",
    "payment_status": "payment_status",
    "payment_date": "payment_date",
    "subscription_date": "subscription_date",
    "subscription_date_ist": "subscription_date_ist",
    "payment_date_ist": "payment_date_ist",
    "is_upgrade": "is_upgrade",
    "paid_amount": "paid_amount",
    "paid_amount_*_100/118": "net_amount",
    "IGST_18%_if_not_KA": "igst_amount",
    "CGST_9%_if_KA": "cgst_amount",
    "SGST_9%_if_KA": "sgst_amount",
    "Duration_in_Days": "duration_in_days",
    "Destination_State": "destination_state",
    "currency": "currency",
    "payment_ref_id": "payment_ref_id",
    "Plan_ID": "plan_id",
    "Plan_Title": "plan_title",
    "Plan_Description": "plan_description",
    "Plan_Duration_in_Month": "plan_duration_in_month",
    "Addon_Plan_IDs": "addon_plan_ids",
    "Addon_Plans_Title": "addon_plans_title",
    "user_id": "user_id",
    "city": "city",
    "state": "state",
    "college_id": "college_id",
    "college_name": "college_name",
    "college_state": "college_state",
    "year_of_admission": "year_of_admission",
    "coupon": "coupon",
    "coupon_discount": "coupon_discount",
    "coupon_agent": "coupon_agent",
    "loyalty_discount_applied": "loyalty_discount_applied",
    "eoi": "eoi",
    "termination_remark": "termination_remark",
    "terminated_by": "terminated_by",
    "termination_date": "termination_date",
    "course_id": "course_id",
    "reference_id": "reference_id",
    "gift_plan_payment_ref_id": "gift_plan_payment_ref_id",
}

REQUIRED = [
    "payment_id", "payment_status", "payment_date_ist",
    "paid_amount", "paid_amount_*_100/118", "coupon",
]


def read_file(filename: str, content: bytes) -> pd.DataFrame:
    if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    return pd.read_csv(io.BytesIO(content), low_memory=False)


def validate(df: pd.DataFrame) -> list[ValidationError]:
    """Structural and type validation. Runs before anything is qualified."""
    errors: list[ValidationError] = []

    for col in REQUIRED:
        if col not in df.columns:
            errors.append(ValidationError(
                row_number=0, field=col, error_code="MISSING_COLUMN",
                message=f"The file has no '{col}' column. Export the full sales extract and try again.",
            ))
    if errors:
        return errors

    for idx, row in df.iterrows():
        n = int(idx) + 2  # 1-based, allowing for the header row
        pid = row.get("payment_id")
        if pd.isna(pid) or not str(pid).strip():
            errors.append(ValidationError(
                row_number=n, field="payment_id", error_code="MISSING_PAYMENT_ID",
                message="Payment id is blank.",
            ))
        if pd.isna(row.get("payment_date_ist")):
            errors.append(ValidationError(
                row_number=n, payment_id=str(pid), field="payment_date_ist",
                error_code="INVALID_DATE", message="Payment date is blank or unreadable.",
            ))
        for money in ("paid_amount", "paid_amount_*_100/118"):
            v = row.get(money)
            if pd.isna(v) or not isinstance(v, (int, float)):
                errors.append(ValidationError(
                    row_number=n, payment_id=str(pid), field=money,
                    error_code="INVALID_AMOUNT",
                    message=f"'{money}' is not a number.",
                ))
    return errors


def to_transactions(df: pd.DataFrame) -> list[SalesTransaction]:
    out = []
    for _, r in df.iterrows():
        out.append(SalesTransaction(
            payment_id=str(r["payment_id"]),
            order_id=_s(r.get("order_id")),
            invoice_id=_s(r.get("invoice_id")),
            payment_status=str(r["payment_status"]),
            payment_date_ist=pd.to_datetime(r["payment_date_ist"]).to_pydatetime(),
            paid_amount=float(r["paid_amount"]),
            net_amount=float(r["paid_amount_*_100/118"]),
            plan_id=_i(r.get("Plan_ID")),
            plan_title=_s(r.get("Plan_Title")),
            plan_duration_in_month=_i(r.get("Plan_Duration_in_Month")),
            course_id=_i(r.get("course_id")),
            user_id=_s(r.get("user_id")),
            college_id=_s(r.get("college_id")),
            college_name=_s(r.get("college_name")),
            coupon=_s(r.get("coupon")),
            coupon_agent=_s(r.get("coupon_agent")),
            state=_s(r.get("state")),
        ))
    return out


def _s(v):
    return None if v is None or pd.isna(v) else str(v)


def _i(v):
    try:
        return None if v is None or pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


def existing_payment_ids(payment_ids: list[str]) -> set[str]:
    """Duplicate detection against everything already imported."""
    if not payment_ids:
        return set()
    s = get_settings()
    found: set[str] = set()
    for i in range(0, len(payment_ids), 5000):
        chunk = payment_ids[i:i + 5000]
        rows = bq.query(
            f"SELECT payment_id FROM {s.table('raw_sales')} "
            "WHERE payment_id IN UNNEST(@ids)",
            {"ids": chunk},
        )
        found.update(r["payment_id"] for r in rows)
    return found


def preview(
    filename: str,
    content: bytes,
    period: str,
    uploaded_by: str,
) -> tuple[UploadSummary, pd.DataFrame, list[SalesTransaction]]:
    """Validate and dry-run the qualification. Writes nothing."""
    batch_id = f"BATCH-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    df = read_file(filename, content)
    summary = UploadSummary(
        batch_id=batch_id,
        filename=filename,
        uploaded_by=uploaded_by,
        uploaded_at=datetime.now(timezone.utc),
        total_rows=len(df),
    )

    errors = validate(df)
    if any(e.error_code == "MISSING_COLUMN" for e in errors):
        summary.validation_status = "FAILED"
        summary.invalid_rows = len(df)
        summary.errors = [e.model_dump() for e in errors]
        return summary, df, []

    bad_rows = {e.row_number for e in errors}
    clean = df.drop(index=[r - 2 for r in bad_rows if 0 <= r - 2 < len(df)], errors="ignore")

    txns = to_transactions(clean)
    already = existing_payment_ids([t.payment_id for t in txns])
    signatures = coupon_service.load_for_period(period)

    qres = run_qualification(txns, signatures, seen_payment_ids=already)

    unattributed = [t for t in qres.transactions if t.employee_id is None]
    summary.duplicate_rows = sum(
        1 for t in unattributed if t.reason and t.reason.value == "DUPLICATE_PAYMENT_ID"
    )
    summary.unknown_coupons = sum(
        1 for t in unattributed if t.reason and t.reason.value == "COUPON_NOT_FOUND"
    )
    summary.unknown_initials = len({
        t.payment_id for t in unattributed
        if t.reason and t.reason.value == "COUPON_NOT_FOUND"
    })
    summary.invalid_rows = len(bad_rows)
    summary.valid_rows = len(txns) - summary.duplicate_rows
    summary.qualified_revenue = qres.qualified_revenue
    summary.disqualified_revenue = qres.disqualified_revenue
    summary.validation_status = "VALIDATED" if not errors else "VALIDATED_WITH_ERRORS"
    summary.errors = [e.model_dump() for e in errors[:500]]

    return summary, clean, txns


def commit(
    summary: UploadSummary,
    df: pd.DataFrame,
    period: str,
) -> int:
    """Load the validated rows into raw_sales. Only called after confirmation."""
    renamed = df.rename(columns=COLUMN_MAP)
    keep = [c for c in COLUMN_MAP.values() if c in renamed.columns]
    rows = renamed[keep].where(pd.notna(renamed[keep]), None).to_dict("records")

    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        r["upload_batch_id"] = summary.batch_id
        r["source_file"] = summary.filename
        r["ingested_at"] = now
        r["ingested_by"] = summary.uploaded_by
        for k in ("payment_date", "payment_date_ist", "subscription_date",
                  "subscription_date_ist", "termination_date"):
            if r.get(k) is not None:
                r[k] = str(r[k])

    n = bq.load_rows("raw_sales", rows)
    bq.insert_rows("upload_batches", [{
        "batch_id": summary.batch_id,
        "filename": summary.filename,
        "period": period,
        "uploaded_by": summary.uploaded_by,
        "uploaded_at": summary.uploaded_at.isoformat(),
        "total_rows": summary.total_rows,
        "valid_rows": summary.valid_rows,
        "invalid_rows": summary.invalid_rows,
        "duplicate_rows": summary.duplicate_rows,
        "unknown_coupons": summary.unknown_coupons,
        "validation_status": "IMPORTED",
    }])
    return n
