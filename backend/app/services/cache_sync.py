"""Keep every API instance's caches in step after a write.

Each instance keeps dashboard results, employee lookups and month statuses in
memory for minutes. A write clears the copy on the instance that took it, but
Cloud Run runs several instances, so the others went on serving the old
figures until their own copies expired: an edit in People did not show on My
team for up to five minutes.

Now a write also appends a row to `cache_events`. Every instance checks the
newest event at most every CHECK_SECONDS and, when it is newer than the last
one it saw, drops its caches. The check is one tiny query, so a change shows
everywhere within about CHECK_SECONDS.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from app.db import bigquery as bq

log = logging.getLogger(__name__)

CHECK_SECONDS = 10

_lock = threading.Lock()
_last_check = 0.0
_last_seen: datetime | None = None


def forget_local() -> None:
    """Drop this instance's cached results, lookups and month statuses."""
    from app.services import dashboards, employees, month
    dashboards.forget_results()
    employees.forget_lookups()
    month.forget_statuses()


def record_write(path: str, who: str | None = None) -> None:
    """Tell the other instances that data changed. Never fails the write."""
    global _last_seen
    now = datetime.now(timezone.utc)
    forget_local()
    try:
        bq.insert_rows("cache_events", [{"event_at": now.isoformat(), "path": path, "by": who}])
        with _lock:
            # This instance is already fresh; do not clear it again for its own event.
            _last_seen = max(_last_seen, now) if _last_seen else now
    except Exception:  # noqa: BLE001 - other instances still catch up on their TTL
        log.warning("Could not record cache event for %s", path, exc_info=True)


def check() -> bool:
    """Clear local caches if another instance has written since we last looked.

    Returns True when caches were cleared. Rate-limited to one query every
    CHECK_SECONDS per instance.
    """
    global _last_check, _last_seen
    now = time.monotonic()
    with _lock:
        if now - _last_check < CHECK_SECONDS:
            return False
        _last_check = now
    try:
        rows = bq.query(
            "SELECT MAX(event_at) AS newest FROM "
            f"{_table()} WHERE event_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)"
        )
    except Exception:  # noqa: BLE001 - a failed check must not break the page
        log.warning("Cache event check failed", exc_info=True)
        return False
    newest = rows[0]["newest"] if rows else None
    with _lock:
        stale = newest is not None and (_last_seen is None or newest > _last_seen)
        if newest is not None:
            _last_seen = max(_last_seen, newest) if _last_seen else newest
    if stale:
        forget_local()
    return stale


def _table() -> str:
    from app.config import get_settings
    return get_settings().table("cache_events")
