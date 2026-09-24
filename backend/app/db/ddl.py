"""Apply schema.sql and seed the policy tables from the supplied workbook.

    python -m app.db.ddl --apply
    python -m app.db.ddl --seed-rules
"""
from __future__ import annotations

import argparse
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.db import bigquery as bq
from app.engines.incentive import DEFAULT_SLAB_TABLES

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements.

    Splitting on ';' alone is wrong: a semicolon inside a line comment or a
    string literal is not a statement terminator. This walks the text tracking
    whether it is inside a comment, a quoted string or a backtick-quoted
    identifier, which is the minimum needed to read this file correctly.
    """
    statements, current = [], []
    i, n = 0, len(sql)
    in_line_comment = in_block_comment = False
    quote: str | None = None

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            current.append(ch)
        elif in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                current.append("*/")
                i += 2
                continue
            current.append(ch)
        elif quote:
            if ch == "\\" and nxt:
                current.append(ch + nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            current.append(ch)
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            current.append("--")
            i += 2
            continue
        elif ch == "/" and nxt == "*":
            in_block_comment = True
            current.append("/*")
            i += 2
            continue
        elif ch in "'\"`":
            quote = ch
            current.append(ch)
        elif ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_schema() -> None:
    s = get_settings()
    sql = (
        SCHEMA_PATH.read_text()
        .replace("${PROJECT}", s.gcp_project_id)
        .replace("${DATASET}", s.bq_dataset)
        .replace("${LOCATION}", s.bq_location)
    )
    statements = split_statements(sql)
    failures = []
    for stmt in statements:
        first = stmt.splitlines()[0][:70]
        try:
            bq.query(stmt)
        except Exception as exc:
            failures.append((first, exc))
            print(f"  FAILED  {first}\n          {exc}")
        else:
            print(f"  ok      {first}")

    print(f"\n{len(statements) - len(failures)} of {len(statements)} statements "
          f"applied to {s.gcp_project_id}.{s.bq_dataset} ({s.bq_location})")
    if failures:
        print("\nSome statements failed. The most common cause is a location "
              "mismatch: BQ_LOCATION must match the dataset holding your sales "
              "table, and a dataset's location cannot be changed after creation.")
        raise SystemExit(1)


def seed_rules(effective_from: date = date(2026, 4, 1)) -> None:
    """Seed incentive_rules from the Policy sheet values."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for scope, table in DEFAULT_SLAB_TABLES.items():
        for slab in table.slabs:
            rows.append({
                "rule_id": str(uuid.uuid4()),
                "scope": scope.value,
                "vertical": "Marrow",
                "threshold": slab.threshold,
                "rate": slab.rate,
                "effective_from": effective_from.isoformat(),
                "source_note": table.source_note,
                "created_by": "seed",
                "created_at": now,
            })
    bq.append_rows("incentive_rules", rows)
    print(f"seeded {len(rows)} incentive rule rows")

    from app.services import coupon_rules
    n = coupon_rules.ensure_seeded()
    print(f"seeded {n} coupon rule rows" if n else "coupon rules already present")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--seed-rules", action="store_true")
    a = p.parse_args()
    if a.apply:
        apply_schema()
    if a.seed_rules:
        seed_rules()
