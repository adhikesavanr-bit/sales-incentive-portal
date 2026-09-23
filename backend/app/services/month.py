"""Month lifecycle: OPEN -> UNDER_REVIEW -> APPROVED -> LOCKED."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.config import get_settings
from app.db import bigquery as bq
from app.models.schemas import MonthStatus

_ALLOWED = {
    MonthStatus.OPEN: {MonthStatus.UNDER_REVIEW},
    MonthStatus.UNDER_REVIEW: {MonthStatus.OPEN, MonthStatus.APPROVED},
    MonthStatus.APPROVED: {MonthStatus.UNDER_REVIEW, MonthStatus.LOCKED},
    MonthStatus.LOCKED: {MonthStatus.OPEN},  # reopening needs REOPEN_MONTH
}


def get_status(period: str) -> MonthStatus:
    s = get_settings()
    rows = bq.query(
        f"SELECT status FROM {s.table('month_status')} WHERE period = @p "
        "ORDER BY changed_at DESC LIMIT 1",
        {"p": period},
    )
    return MonthStatus(rows[0]["status"]) if rows else MonthStatus.OPEN


def require_open(period: str) -> None:
    """Guard for anything that would change a month's numbers."""
    current = get_status(period)
    if current is MonthStatus.LOCKED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{period} is locked. Reopen it before making changes.",
        )
    if current is MonthStatus.APPROVED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{period} is approved. Move it back to review before making changes.",
        )


def transition(period: str, to: MonthStatus, changed_by: str, reason: str) -> MonthStatus:
    current = get_status(period)
    if to not in _ALLOWED[current]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{period} cannot move from {current.value} to {to.value}.",
        )
    bq.insert_rows("month_status", [{
        "period": period,
        "status": to.value,
        "changed_by": changed_by,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }])
    return to
