"""Month lifecycle: OPEN -> UNDER_REVIEW -> APPROVED -> LOCKED."""
from __future__ import annotations

import threading
import time
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


# Every dashboard request now asks whether its month is published, so the
# answer is kept briefly. A change made here clears it on this instance at
# once; other instances follow within the TTL.
_STATUS_TTL_SECONDS = 30
_status_cache: dict[str, tuple[float, MonthStatus]] = {}
_status_lock = threading.Lock()

# A month's results are shown to the sales line only from approval onwards.
PUBLISHED = frozenset({MonthStatus.APPROVED, MonthStatus.LOCKED})


def forget_statuses() -> None:
    with _status_lock:
        _status_cache.clear()


def get_status(period: str) -> MonthStatus:
    with _status_lock:
        hit = _status_cache.get(period)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    s = get_settings()
    rows = bq.query(
        f"SELECT status FROM {s.table('month_status')} WHERE period = @p "
        "ORDER BY changed_at DESC LIMIT 1",
        {"p": period},
    )
    current = MonthStatus(rows[0]["status"]) if rows else MonthStatus.OPEN
    with _status_lock:
        _status_cache[period] = (time.monotonic() + _STATUS_TTL_SECONDS, current)
    return current


def is_published(period: str) -> bool:
    return get_status(period) in PUBLISHED


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
    forget_statuses()
    return to
