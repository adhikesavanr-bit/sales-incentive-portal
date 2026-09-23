"""Dashboard reads. Always from the aggregated view, never from raw_sales."""
from __future__ import annotations

from app.auth.rbac import Principal, visible_employee_sql
from app.config import get_settings
from app.db import bigquery as bq

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


def daily_trend(employee_ids: list[str], period: str) -> list[dict]:
    s = get_settings()
    return bq.query(
        f"""
        SELECT DATE(r.payment_date_ist, 'Asia/Kolkata') AS day,
               COUNT(*) AS units,
               SUM(r.net_amount) AS net_revenue,
               SUM(CASE WHEN q.status = 'QUALIFIED' THEN r.net_amount ELSE 0 END) AS qualified_revenue
        FROM {s.table('raw_sales')} r
        JOIN {s.table('transaction_qualification')} q USING (payment_id)
        WHERE q.period = @p AND q.employee_id IN UNNEST(@ids)
        GROUP BY day ORDER BY day
        """,
        {"p": period, "ids": employee_ids},
    )


def plan_mix(employee_ids: list[str], period: str) -> list[dict]:
    s = get_settings()
    return bq.query(
        f"""
        SELECT r.plan_title, r.plan_duration_in_month,
               COUNT(*) AS units, SUM(r.net_amount) AS net_revenue
        FROM {s.table('raw_sales')} r
        JOIN {s.table('transaction_qualification')} q USING (payment_id)
        WHERE q.period = @p AND q.employee_id IN UNNEST(@ids)
        GROUP BY r.plan_title, r.plan_duration_in_month
        ORDER BY net_revenue DESC
        """,
        {"p": period, "ids": employee_ids},
    )


def transactions(employee_id: str, period: str, limit: int = 200, offset: int = 0) -> list[dict]:
    s = get_settings()
    return bq.query(
        f"""
        SELECT r.payment_date_ist, r.payment_id, r.invoice_id, r.plan_title,
               r.plan_duration_in_month, r.college_name, r.coupon,
               r.paid_amount, r.net_amount,
               q.status, q.disqualification_reason, q.reason_detail
        FROM {s.table('raw_sales')} r
        JOIN {s.table('transaction_qualification')} q USING (payment_id)
        WHERE q.period = @p AND q.employee_id = @id
        ORDER BY r.payment_date_ist DESC
        LIMIT @lim OFFSET @off
        """,
        {"p": period, "id": employee_id, "lim": limit, "off": offset},
    )
