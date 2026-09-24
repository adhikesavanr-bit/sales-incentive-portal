"""Dashboard reads: the aggregated view for figures, the source table for sale detail."""
from __future__ import annotations

import logging
import threading
import time

from app.auth.rbac import Principal, visible_employee_sql
from app.config import get_settings
from app.db import bigquery as bq
from app.services import source_tables

log = logging.getLogger(__name__)

# Every column here must be table-qualified: employee_id, is_active, region,
# zone and designation all exist on BOTH v_employee_hierarchy and
# monthly_incentive, so a bare name is an ambiguous-column error at runtime.
_METRICS = """
  SUM(target_units)         AS target_units,
  SUM(achieved_units)       AS achieved_units,
  SUM(target_revenue)       AS target_revenue,
  SUM(gross_revenue)        AS gross_revenue,
  SUM(qualified_revenue)    AS qualified_revenue,
  SUM(disqualified_revenue) AS disqualified_revenue,
  SUM(total_incentive)      AS incentive_liability,
  SUM(net_payable)          AS net_payable,
  COUNT(DISTINCT CASE WHEN m.is_active THEN m.employee_id END) AS headcount
"""


def own(employee_id: str, period: str) -> dict | None:
    s = get_settings()
    rows = bq.query(
        f"SELECT * FROM {s.table('v_incentive_current')} "
        "WHERE employee_id = @id AND period = @p",
        {"id": employee_id, "p": period},
    )
    return rows[0] if rows else None


def team_rows(principal: Principal, period: str) -> list[dict]:
    """Every employee in scope, one row each, ranked by achievement."""
    s = get_settings()
    scope, params = visible_employee_sql(principal)
    return bq.query(
        f"""
        SELECT h.employee_id, h.full_name, h.designation, h.region, h.zone,
               m.target_units, m.achieved_units, m.unit_pct,
               m.target_revenue, m.qualified_revenue, m.disqualified_revenue,
               m.revenue_pct, m.base_pct, m.arpu,
               m.total_incentive, m.net_payable, m.accumulation, m.is_active
        FROM {s.table('v_employee_hierarchy')} h
        LEFT JOIN {s.table('v_incentive_current')} m
          ON m.employee_id = h.employee_id AND m.period = @p
        WHERE {scope}
        ORDER BY m.base_pct DESC NULLS LAST
        """,
        {**params, "p": period},
    )


def summary(principal: Principal, period: str) -> dict:
    """One aggregate row for the KPI strip.

    Employees with no sales are excluded from headcount and per-head averages
    (DATA_MAPPING.md §7.4) but their rows still appear in the table.
    """
    s = get_settings()
    scope, params = visible_employee_sql(principal)
    rows = bq.query(
        f"""
        SELECT {_METRICS}
        FROM {s.table('v_incentive_current')} m
        JOIN {s.table('v_employee_hierarchy')} h USING (employee_id)
        WHERE m.period = @p AND {scope}
        """,
        {**params, "p": period},
    )
    r = rows[0] if rows else {}
    target_rev = float(r.get("target_revenue") or 0)
    qualified = float(r.get("qualified_revenue") or 0)
    disqualified = float(r.get("disqualified_revenue") or 0)
    gross_net = qualified + disqualified
    r["achievement_pct"] = (qualified / target_rev) if target_rev else 0.0
    r["qualification_pct"] = (qualified / gross_net) if gross_net else 0.0
    return r


def group_by(principal: Principal, period: str, dimension: str) -> list[dict]:
    """Roll up one level down: region, zone, submanager or plan."""
    allowed = {
        "region": "h.region",
        "zone": "h.zone",
        "submanager": "h.submanager_id",
        "rm": "h.rm_id",
    }
    if dimension not in allowed:
        raise ValueError(f"Cannot group by '{dimension}'.")
    s = get_settings()
    scope, params = visible_employee_sql(principal)
    col = allowed[dimension]
    return bq.query(
        f"""
        SELECT {col} AS group_key, {_METRICS}
        FROM {s.table('v_incentive_current')} m
        JOIN {s.table('v_employee_hierarchy')} h USING (employee_id)
        WHERE m.period = @p AND {scope}
        GROUP BY group_key
        ORDER BY qualified_revenue DESC
        """,
        {**params, "p": period},
    )


def _latest_run(period: str) -> str:
    """The period's current qualification rows.

    Every recalculation appends a full copy, so reading the table unfiltered
    counts each sale once per run. Only the latest version is current, which
    is also the version `v_incentive_current` shows.
    """
    s = get_settings()
    t = s.table("transaction_qualification")
    return (
        f"SELECT * FROM {t} WHERE period = @p AND calculation_version = "
        f"(SELECT MAX(calculation_version) FROM {t} WHERE period = @p)"
    )


# Building the sales SELECT costs two BigQuery round trips before the real
# query can start: the period's source_table_config row, then the table's
# INFORMATION_SCHEMA. Both change only when an admin repoints a period, so
# the SQL is kept for a few minutes. Repointing clears it on this instance;
# other instances pick the change up when their copy expires.
_SALES_SQL_TTL_SECONDS = 300
_sales_sql_cache: dict[str, tuple[float, str]] = {}
_sales_sql_lock = threading.Lock()


def forget_sales_sql(period: str | None = None) -> None:
    with _sales_sql_lock:
        if period is None:
            _sales_sql_cache.clear()
        else:
            _sales_sql_cache.pop(period, None)


def _sales_sql(period: str) -> str:
    """The period's sales, display columns only, from wherever they live.

    Most months are read straight from a configured source table rather than
    uploaded into raw_sales, so the dashboards must read from the same place
    the calculation did. If the source cannot be read, the sales still list
    from the qualification rows, just without plan/college detail.
    """
    with _sales_sql_lock:
        hit = _sales_sql_cache.get(period)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    s = get_settings()
    src = source_tables.resolve(period)
    if src is not None:
        try:
            sql = source_tables.display_sql(period, src)
        except Exception:  # noqa: BLE001 - degrade to amounts only, never 500
            # Not cached: the next request tries the source again.
            log.exception("could not read sales detail for %s from %s", period, src.label)
            cols = ", ".join(
                f"CAST(NULL AS {'TIMESTAMP' if c == 'payment_date_ist' else 'INT64' if c == 'plan_duration_in_month' else 'STRING'}) AS {c}"
                for c in source_tables.DISPLAY_COLUMNS
            )
            return f"SELECT {cols} FROM UNNEST([1]) WHERE FALSE"
    else:
        sql = (
            f"SELECT {', '.join(source_tables.DISPLAY_COLUMNS)} FROM {s.table('raw_sales')} "
            "WHERE FORMAT_TIMESTAMP('%Y-%m', payment_date_ist, 'Asia/Kolkata') = @p"
        )
    with _sales_sql_lock:
        _sales_sql_cache[period] = (time.monotonic() + _SALES_SQL_TTL_SECONDS, sql)
    return sql


def _with(period: str) -> str:
    return f"WITH q AS ({_latest_run(period)}), sales AS ({_sales_sql(period)})"


def daily_trend(employee_ids: list[str], period: str) -> list[dict]:
    return bq.query(
        f"""
        {_with(period)}
        SELECT DATE(r.payment_date_ist, 'Asia/Kolkata') AS day,
               COUNT(*) AS units,
               SUM(q.net_amount) AS net_revenue,
               SUM(CASE WHEN q.status = 'QUALIFIED' THEN q.net_amount ELSE 0 END) AS qualified_revenue
        FROM q JOIN sales r ON r.payment_id = q.payment_id
        WHERE q.employee_id IN UNNEST(@ids)
        GROUP BY day ORDER BY day
        """,
        {"p": period, "ids": employee_ids},
    )


def plan_mix(employee_ids: list[str], period: str) -> list[dict]:
    return bq.query(
        f"""
        {_with(period)}
        SELECT r.plan_title, r.plan_duration_in_month,
               COUNT(*) AS units, SUM(q.net_amount) AS net_revenue
        FROM q JOIN sales r ON r.payment_id = q.payment_id
        WHERE q.employee_id IN UNNEST(@ids)
        GROUP BY r.plan_title, r.plan_duration_in_month
        ORDER BY net_revenue DESC
        """,
        {"p": period, "ids": employee_ids},
    )


def transactions(employee_id: str, period: str, limit: int = 200, offset: int = 0) -> list[dict]:
    """One person's sales for the period, with each sale's verdict.

    Starts from the qualification rows, so every counted sale is listed even
    when its detail row is missing from the source.
    """
    return bq.query(
        f"""
        {_with(period)}
        SELECT r.payment_date_ist, q.payment_id, r.invoice_id, r.plan_title,
               r.plan_duration_in_month, r.college_id, r.college_name, r.coupon,
               q.paid_amount, q.net_amount,
               q.status, q.disqualification_reason, q.reason_detail
        FROM q LEFT JOIN sales r ON r.payment_id = q.payment_id
        WHERE q.employee_id = @id
        ORDER BY r.payment_date_ist DESC
        LIMIT @lim OFFSET @off
        """,
        {"p": period, "id": employee_id, "lim": limit, "off": offset},
    )


def coupon_analysis(employee_id: str, period: str) -> list[dict]:
    """Each coupon's verdict for the month: the "Coupon Analysis" block of the
    per-person workbook (college, coupon, group size, total, qualified).

    Reads the latest run only; every recalculation appends a new copy, and all
    rows of one run share its calculated_at.
    """
    s = get_settings()
    sq = s.table("signature_qualification")
    return bq.query(
        f"""
        WITH latest AS (
          SELECT * FROM {sq}
          WHERE period = @p
            AND calculated_at = (SELECT MAX(calculated_at) FROM {sq} WHERE period = @p)
        ),
        master AS (
          SELECT coupon_signature, ANY_VALUE(college_id) AS college_id,
                 ANY_VALUE(group_size) AS group_size,
                 ANY_VALUE(required_sales) AS required_sales,
                 ANY_VALUE(activation_date) AS activation_date
          FROM {s.table('coupon_master')} GROUP BY coupon_signature
        )
        SELECT l.coupon_signature, l.coupon_code, m.college_id, m.group_size,
               m.required_sales, m.activation_date,
               l.total, l.club_sales, l.min_sales, l.min_own_sales,
               l.is_foundation, l.qualification_3 AS qualified,
               l.overridden, l.override_reason
        FROM latest l LEFT JOIN master m USING (coupon_signature)
        WHERE l.employee_id = @id AND l.total > 0
        ORDER BY l.qualification_3, m.college_id, l.coupon_code
        """,
        {"p": period, "id": employee_id},
    )
