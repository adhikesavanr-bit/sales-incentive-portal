"""Audit log. Every state change goes through here."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import bigquery as bq


def record(
    user_email: str,
    action: str,
    *,
    entity_type: str | None = None,
    affected_record: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    ip_address: str | None = None,
) -> None:
    bq.insert_rows("audit_log", [{
        "audit_id": str(uuid.uuid4()),
        "user_email": user_email,
        "action": action,
        "entity_type": entity_type,
        "affected_record": affected_record,
        "old_value": json.dumps(old_value, default=str) if old_value is not None else None,
        "new_value": json.dumps(new_value, default=str) if new_value is not None else None,
        "reason": reason,
        "ip_address": ip_address,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }])


def record_many(
    user_email: str,
    action: str,
    entries: list[dict],
    *,
    reason: str | None = None,
) -> None:
    """Several audit rows in one insert. Each entry takes `record`'s keywords."""
    now = datetime.now(timezone.utc).isoformat()
    bq.insert_rows("audit_log", [{
        "audit_id": str(uuid.uuid4()),
        "user_email": user_email,
        "action": action,
        "entity_type": e.get("entity_type"),
        "affected_record": e.get("affected_record"),
        "old_value": json.dumps(e["old_value"], default=str) if e.get("old_value") is not None else None,
        "new_value": json.dumps(e["new_value"], default=str) if e.get("new_value") is not None else None,
        "reason": reason,
        "ip_address": None,
        "occurred_at": now,
    } for e in entries])


def search(
    action: str | None = None,
    user_email: str | None = None,
    limit: int = 200,
) -> list[dict]:
    s = get_settings()
    clauses, params = ["TRUE"], {"lim": min(limit, 1000)}
    if action:
        clauses.append("action = @action")
        params["action"] = action
    if user_email:
        clauses.append("user_email = @user_email")
        params["user_email"] = user_email
    return bq.query(
        f"SELECT * FROM {s.table('audit_log')} WHERE {' AND '.join(clauses)} "
        "ORDER BY occurred_at DESC LIMIT @lim",
        params,
    )
