"""Where each month's sales come from.

The sales live in one table per month, versioned — `Aug_2026_v1`, and a `_v2`
when a month is re-cut. So the source is a mapping from period to table, held
as data:

    1. `source_table_config` in BigQuery — an explicit row for a period. Wins.
    2. `SOURCE_TABLE_TEMPLATE` — a naming pattern, for months nobody has
       overridden.
    3. Neither — the period falls back to uploaded `raw_sales`.

Changing where a month reads from is a row, not a deployment. A locked month
keeps the table it was calculated against, because the calculation is stored;
re-pointing a locked month and recalculating requires reopening it first.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import get_settings
from app.db import bigquery as bq
from app.models.schemas import SalesTransaction

log = logging.getLogger(__name__)

# Source column -> the name the engines use. Only these six are required; the
# rest are carried through when present. Values are matched case-insensitively
# and ignoring spaces and underscores, so `Payment ID`, `payment_id` and
# `PaymentId` all resolve.
CANONICAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "payment_id": ("payment_id", "paymentid"),
    "payment_status": ("payment_status", "status", "paymentstatus"),
    "payment_date_ist": ("payment_date_ist", "payment_date", "paymentdateist", "paymentdate"),
    "paid_amount": ("paid_amount", "paidamount", "amount", "gross_amount"),
    "net_amount": (
        "net_amount", "paid_amount_100_118", "paid_amount_x_100_118",
        "netamount", "amount_excl_gst", "paid_amount_excl_gst",
    ),
    "coupon": ("coupon", "coupon_code", "couponcode"),
}

OPTIONAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "order_id": ("order_id",),
    "invoice_id": ("invoice_id",),
    "plan_id": ("plan_id",),
    "plan_title": ("plan_title",),
    "plan_duration_in_month": ("plan_duration_in_month", "plan_duration"),
    "course_id": ("course_id",),
    "user_id": ("user_id",),
    "college_id": ("college_id",),
    "college_name": ("college_name",),
    "coupon_agent": ("coupon_agent", "agent", "bde_initial"),
    "state": ("state",),
}

REQUIRED = tuple(CANONICAL_COLUMNS)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _load_json(value) -> dict | None:
    """A BigQuery JSON column comes back as a string from some clients and as a
    parsed object from others. Accept either."""
    if not value:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


@dataclass
class SourceTable:
    project: str
    dataset: str
    table: str
    date_column: str = "payment_date_ist"
    column_map: dict[str, str] | None = None   # canonical -> actual
    origin: str = "config"                     # config | template | raw_sales

    @property
    def ref(self) -> str:
        return f"`{self.project}.{self.dataset}.{self.table}`"

    @property
    def label(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"


def render_template(template: str, period: str) -> str:
    """Turn a naming pattern plus a period into a table name.

    Placeholders: {MMM} Aug, {MM} 08, {YYYY} 2026, {YY} 26, {period} 2026-08.
    The default pattern `{MMM}_{YYYY}_v1` yields `Aug_2026_v1`.
    """
    d = datetime.strptime(period, "%Y-%m")
    return (
        template
        .replace("{MMM}", d.strftime("%b"))
        .replace("{MM}", d.strftime("%m"))
        .replace("{YYYY}", d.strftime("%Y"))
        .replace("{YY}", d.strftime("%y"))
        .replace("{period}", period)
    )


def resolve(period: str) -> SourceTable | None:
    """Which table holds this period's sales. None means use uploaded data."""
    s = get_settings()

    rows = bq.query(
        f"SELECT * FROM {s.table('source_table_config')} "
        "WHERE period = @p AND is_active ORDER BY updated_at DESC LIMIT 1",
        {"p": period},
    )
    if rows:
        r = rows[0]
        return SourceTable(
            project=r.get("source_project") or s.gcp_project_id,
            dataset=r["source_dataset"],
            table=r["source_table"],
            date_column=r.get("date_column") or "payment_date_ist",
            column_map=_load_json(r.get("column_map")),
            origin="config",
        )

    if s.source_dataset and s.source_table_template:
        return SourceTable(
            project=s.source_project or s.gcp_project_id,
            dataset=s.source_dataset,
            table=render_template(s.source_table_template, period),
            date_column=s.source_date_column,
            origin="template",
        )
    return None


def describe(src: SourceTable) -> list[dict]:
    """Column names and types, straight from INFORMATION_SCHEMA."""
    return bq.query(
        f"""
        SELECT column_name, data_type
        FROM `{src.project}.{src.dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @t
        ORDER BY ordinal_position
        """,
        {"t": src.table},
    )


def map_columns(columns: list[str]) -> tuple[dict[str, str], list[str]]:
    """Match a table's real columns to the names the engines expect.

    Returns the mapping and the list of required names that could not be found,
    so a misconfigured table fails with "no column matching net_amount" rather
    than a SQL error nobody can read.
    """
    by_key = {_key(c): c for c in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in {**CANONICAL_COLUMNS, **OPTIONAL_COLUMNS}.items():
        for alias in aliases:
            hit = by_key.get(_key(alias))
            if hit:
                mapping[canonical] = hit
                break
    missing = [c for c in REQUIRED if c not in mapping]
    return mapping, missing


# Column types that BigQuery can range-filter, and therefore prune partitions
# on. Anything else has to be converted, which forces a full scan.
_RANGE_FILTERABLE = {"TIMESTAMP", "DATETIME", "DATE"}


def period_predicate(date_col: str, data_type: str | None) -> str:
    """The WHERE clause that selects one IST month.

    Written as a half-open range on the raw column rather than
    `FORMAT_TIMESTAMP(...) = @p`, because wrapping the column in a function
    stops BigQuery pruning partitions — on a table holding several years that
    is the difference between scanning one month and scanning all of it.

    `TIMESTAMP(date, 'Asia/Kolkata')` gives midnight IST as a UTC instant, so
    the boundaries land where the business day does rather than at UTC
    midnight, which would pull in 5.5 hours of the neighbouring month.
    """
    if (data_type or "").upper() in _RANGE_FILTERABLE:
        return (
            f"`{date_col}` >= TIMESTAMP(PARSE_DATE('%Y-%m', @p), 'Asia/Kolkata')\n"
            f"          AND `{date_col}` <  TIMESTAMP("
            f"DATE_ADD(PARSE_DATE('%Y-%m', @p), INTERVAL 1 MONTH), 'Asia/Kolkata')"
        )
    # A date held as a string still works, just without pruning.
    return f"FORMAT_TIMESTAMP('%Y-%m', TIMESTAMP(`{date_col}`), 'Asia/Kolkata') = @p"


def _select_list(mapping: dict[str, str]) -> str:
    parts = []
    for canonical in list(CANONICAL_COLUMNS) + list(OPTIONAL_COLUMNS):
        actual = mapping.get(canonical)
        parts.append(
            f"`{actual}` AS {canonical}" if actual else f"CAST(NULL AS STRING) AS {canonical}"
        )
    return ",\n               ".join(parts)


def resolved_mapping(src: SourceTable, period: str) -> tuple[dict[str, str], dict[str, str]]:
    """The canonical -> actual column mapping, and the table's column types."""
    schema = {c["column_name"]: c["data_type"] for c in describe(src)}
    mapping = src.column_map
    if not mapping:
        mapping, missing = map_columns(list(schema))
        if missing:
            raise ValueError(
                f"{src.label} has no column matching: {', '.join(missing)}. "
                f"Set an explicit column_map for {period} if the names differ."
            )
    return mapping, schema


# What the dashboards show for each sale. Kept narrow: these tables are wide.
DISPLAY_COLUMNS = (
    "payment_id", "payment_date_ist", "invoice_id", "plan_title",
    "plan_duration_in_month", "college_id", "college_name", "coupon",
)


def display_sql(period: str, src: SourceTable) -> str:
    """SELECT one month of sales from the source table, display columns only,
    with canonical names and join-safe types. Takes the @p parameter."""
    mapping, schema = resolved_mapping(src, period)
    parts = []
    for canonical in DISPLAY_COLUMNS:
        actual = mapping.get(canonical)
        if not actual:
            kind = "INT64" if canonical == "plan_duration_in_month" else "STRING"
            kind = "TIMESTAMP" if canonical == "payment_date_ist" else kind
            parts.append(f"CAST(NULL AS {kind}) AS {canonical}")
        elif canonical == "payment_date_ist" and schema.get(actual, "").upper() != "TIMESTAMP":
            parts.append(f"TIMESTAMP(`{actual}`) AS {canonical}")
        elif canonical in ("payment_id", "college_id"):
            parts.append(f"CAST(`{actual}` AS STRING) AS {canonical}")
        else:
            parts.append(f"`{actual}` AS {canonical}")
    date_col = mapping.get("payment_date_ist", src.date_column)
    return (
        f"SELECT {', '.join(parts)} FROM {src.ref} "
        f"WHERE {period_predicate(date_col, schema.get(date_col))}"
    )


def load_transactions(period: str, src: SourceTable) -> list[SalesTransaction]:
    """Read one month straight from the source table.

    The date filter is on the partitioning column, so this scans one month even
    when the table holds more. Column names are aliased in SQL rather than
    renamed in Python, so a wide table never crosses the network in full.
    """
    mapping, schema = resolved_mapping(src, period)
    date_col = mapping.get("payment_date_ist", src.date_column)
    rows = bq.query(
        f"""
        SELECT {_select_list(mapping)}
        FROM {src.ref}
        WHERE {period_predicate(date_col, schema.get(date_col))}
        """,
        {"p": period},
    )
    log.info("loaded %s rows for %s from %s", len(rows), period, src.label)

    out = []
    for r in rows:
        out.append(
            SalesTransaction(
                payment_id=str(r["payment_id"]),
                order_id=r.get("order_id"),
                invoice_id=r.get("invoice_id"),
                payment_status=str(r["payment_status"]),
                payment_date_ist=r["payment_date_ist"],
                paid_amount=float(r["paid_amount"] or 0),
                net_amount=float(r["net_amount"] or 0),
                plan_id=r.get("plan_id"),
                plan_title=r.get("plan_title"),
                plan_duration_in_month=r.get("plan_duration_in_month"),
                course_id=r.get("course_id"),
                user_id=r.get("user_id"),
                college_id=str(r["college_id"]) if r.get("college_id") else None,
                college_name=r.get("college_name"),
                coupon=r.get("coupon"),
                coupon_agent=r.get("coupon_agent"),
                state=r.get("state"),
            )
        )
    return out


def set_for_period(
    period: str,
    dataset: str,
    table: str,
    changed_by: str,
    project: str | None = None,
    date_column: str = "payment_date_ist",
    column_map: dict[str, str] | None = None,
) -> SourceTable:
    """Point a period at a table. Previous rows stay, marked inactive."""
    s = get_settings()
    bq.query(
        f"UPDATE {s.table('source_table_config')} SET is_active = FALSE "
        "WHERE period = @p AND is_active",
        {"p": period},
    )
    bq.append_rows("source_table_config", [{
        "period": period,
        "source_project": project or s.gcp_project_id,
        "source_dataset": dataset,
        "source_table": table,
        "date_column": date_column,
        # A JSON column takes a JSON string on insert, not a Python dict.
        "column_map": json.dumps(column_map) if column_map else None,
        "is_active": True,
        "updated_by": changed_by,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }])
    return SourceTable(
        project=project or s.gcp_project_id,
        dataset=dataset,
        table=table,
        date_column=date_column,
        column_map=column_map,
    )


def history(period: str | None = None) -> list[dict]:
    s = get_settings()
    where = "WHERE period = @p" if period else ""
    return bq.query(
        f"SELECT * FROM {s.table('source_table_config')} {where} "
        "ORDER BY period DESC, updated_at DESC",
        {"p": period} if period else {},
    )
