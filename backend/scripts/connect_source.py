"""Point a period at a BigQuery table holding its sales, and check it works.

    # look, change nothing
    python -m scripts.connect_source --period 2026-08 \
        --dataset Monthly_Sub --table Aug_2026_v1 --dry-run

    # save it
    python -m scripts.connect_source --period 2026-08 \
        --dataset Monthly_Sub --table Aug_2026_v1 \
        --reason "August sales cut v1"

The check is the point: it reads the table's columns, matches them to what the
engines need, counts the rows that fall in the period, and reports what it
found before anything is written.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import bigquery as bq  # noqa: E402
from app.services import source_tables  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--project", default=None, help="defaults to GCP_PROJECT_ID")
    ap.add_argument("--date-column", default="payment_date_ist")
    ap.add_argument("--reason", default="Connected via connect_source")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    s = get_settings()

    # The app's own dataset has to exist before a source can be recorded.
    # Checking up front avoids validating the table and then failing on write.
    try:
        bq.query(
            f"SELECT 1 FROM "
            f"`{s.gcp_project_id}.{s.bq_dataset}.INFORMATION_SCHEMA.TABLES` "
            "WHERE table_name = 'source_table_config' LIMIT 1"
        )
    except Exception:
        print(f"The dataset {s.gcp_project_id}.{s.bq_dataset} is not set up yet.\n")
        print("Create it first:")
        print("    python -m app.db.ddl --apply --seed-rules\n")
        print("Then run this again.")
        return 1

    src = source_tables.SourceTable(
        project=a.project or s.gcp_project_id,
        dataset=a.dataset,
        table=a.table,
        date_column=a.date_column,
    )
    print(f"Source : {src.label}")
    print(f"Period : {a.period}\n")

    # --- does it exist and can we read it? -------------------------------
    try:
        columns = source_tables.describe(src)
    except Exception as exc:
        print(f"Could not read the table: {exc}")
        print("\nCheck that the dataset name is right and that this account has")
        print("BigQuery Data Viewer on it.")
        return 1

    if not columns:
        print("No such table, or no permission to see it.")
        print(f"\nList what is there with:\n    bq ls {src.project}:{a.dataset}")
        return 1

    print(f"{len(columns)} columns found.\n")

    # --- do the columns we need exist? -----------------------------------
    mapping, missing = source_tables.map_columns([c["column_name"] for c in columns])
    print("Required columns:")
    for canonical in source_tables.REQUIRED:
        actual = mapping.get(canonical)
        print(f"  {canonical:20s} {'-> ' + actual if actual else 'NOT FOUND'}")

    optional_hits = [c for c in source_tables.OPTIONAL_COLUMNS if c in mapping]
    print(f"\nOptional columns matched: {len(optional_hits)} of "
          f"{len(source_tables.OPTIONAL_COLUMNS)}")
    if optional_hits:
        print("  " + ", ".join(optional_hits))
    unmatched = [c for c in source_tables.OPTIONAL_COLUMNS if c not in mapping]
    if unmatched:
        print("  missing (the app works without these, with less detail in the UI):")
        print("  " + ", ".join(unmatched))

    if missing:
        print(f"\nCannot use this table: no column matching {', '.join(missing)}.")
        print("Column names from the table, to compare:")
        for c in columns:
            print(f"  {c['column_name']}  ({c['data_type']})")
        return 1

    # --- is the month actually in there? ---------------------------------
    date_col = mapping["payment_date_ist"]
    date_type = {c["column_name"]: c["data_type"] for c in columns}.get(date_col)
    predicate = source_tables.period_predicate(date_col, date_type)
    print(f"\nDate column: {date_col} ({date_type})"
          f"{'' if date_type in ('TIMESTAMP','DATETIME','DATE') else '  — stored as text, so queries cannot prune partitions'}")
    try:
        stats = bq.query(
            f"""
            SELECT COUNT(*) AS rows_in_period,
                   COUNT(DISTINCT `{mapping['payment_id']}`) AS distinct_payments,
                   COUNTIF(`{mapping['coupon']}` IS NULL) AS without_coupon,
                   MIN(TIMESTAMP(`{date_col}`)) AS earliest,
                   MAX(TIMESTAMP(`{date_col}`)) AS latest,
                   SUM(`{mapping['net_amount']}`) AS net_revenue
            FROM {src.ref}
            WHERE {predicate}
            """,
            {"p": a.period},
        )[0]
    except Exception as exc:
        print(f"\nThe table exists but could not be queried: {exc}")
        return 1

    n = stats["rows_in_period"] or 0
    print(f"\nRows for {a.period}: {n:,}")
    if n == 0:
        print("\nNothing in this period. Either the table holds a different month,")
        print(f"or '{date_col}' is not the date the app should filter on.")
        print("Try --date-column with the right column name.")
        return 1

    print(f"  distinct payment ids : {stats['distinct_payments']:,}")
    dupes = n - (stats["distinct_payments"] or 0)
    if dupes:
        print(f"  duplicate payment ids: {dupes:,}  (the engine de-duplicates these)")
    print(f"  without a coupon     : {stats['without_coupon']:,}  "
          f"(these cannot be attributed to a BDE)")
    print(f"  date range           : {stats['earliest']} to {stats['latest']}")
    print(f"  net revenue          : Rs. {float(stats['net_revenue'] or 0):,.2f}")

    status = bq.query(
        f"""
        SELECT `{mapping['payment_status']}` AS status, COUNT(*) AS n
        FROM {src.ref}
        WHERE {predicate}
        GROUP BY status ORDER BY n DESC
        """,
        {"p": a.period},
    )
    print("\n  payment status:")
    for r in status:
        print(f"    {str(r['status']):16s} {r['n']:>8,}")

    if a.dry_run:
        print("\nDry run — nothing saved. Re-run without --dry-run to connect it.")
        return 0

    source_tables.set_for_period(
        a.period, a.dataset, a.table, "connect_source",
        a.project, a.date_column, mapping,
    )
    print(f"\nConnected. {a.period} now reads from {src.label}.")
    print("Recalculate the period to apply it:")
    print(f"    curl -X POST '$API/api/incentive/calculate?period={a.period}&reason=...'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
