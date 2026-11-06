"""Exports. Always scoped: the same predicate that guards the dashboards."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse

from app.auth.deps import require
from app.auth.rbac import Permission, Principal
from app.routers.dashboards import (
    _authorise_target, _months_between, _require_ranges, can_see_month, load_range_months,
)
from app.services import audit, dashboards, month, statement_pdf
from app.services import employees as employee_service

router = APIRouter(prefix="/api/export", tags=["export"])

REPORTS = {
    "performance": "Employee performance",
    "incentive": "Incentive calculation",
    "target-vs-achievement": "Target vs achievement",
    "qualification": "Qualification summary",
}


def _csv(rows: list[dict], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _require_published(principal: Principal, period: str) -> None:
    if not can_see_month(principal, period):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            month.not_published_message(period),
        )


# Declared before /{report}, which would otherwise swallow "statement".
@router.get("/statement")
def export_statement(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    employee_id: str | None = None,
    principal: Principal = Depends(require(Permission.EXPORT_SCOPED)),
):
    """One person's monthly statement as a PDF: their own, or anyone in scope."""
    target = _authorise_target(principal, employee_id, period)
    _require_published(principal, period)
    row = dashboards.own(target, period)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Nothing calculated for this month yet."
        )
    emp = employee_service.get_by_id(target)
    pdf = statement_pdf.build_statement(
        period=period,
        employee=emp.model_dump() if emp else {"employee_id": target},
        breakdown=row,
        generated_by=principal.email,
    )
    audit.record(
        principal.email, "EXPORT", entity_type="statement",
        affected_record=f"{target}:{period}",
        new_value={"format": "pdf"},
    )
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="incentive-{target}-{period}.pdf"'
        },
    )


@router.get("/range")
def export_range(
    start: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    end: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    label: str = Query("range", pattern=r"^[A-Za-z0-9 ._·-]{1,40}$"),
    principal: Principal = Depends(require(Permission.EXPORT_SCOPED)),
):
    """A quarter or financial year as an Excel workbook: totals, people, month by month."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    _require_ranges(principal)
    months = _months_between(start, end)
    data = load_range_months(principal, [m for m in months if can_see_month(principal, m)])
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing calculated for this period yet.")
    merged = dashboards.merge_months([d for _, d in data])
    included = [m for m, _ in data]

    wb = Workbook()
    bold = Font(bold=True)

    def sheet(ws, headers, rows):
        ws.append(headers)
        for c in ws[1]:
            c.font = bold
        for r in rows:
            ws.append(r)
        ws.freeze_panes = "A2"
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = max(14, len(h) + 2)

    s = merged["summary"]
    ws = wb.active
    ws.title = "Summary"
    sheet(ws, ["Measure", "Value"], [
        ["Period", f"{start} to {end}"],
        ["Months included", ", ".join(included)],
        ["Months not included", ", ".join(m for m in months if m not in included) or "—"],
        ["Headcount (with sales)", s["headcount"]],
        ["Target revenue", s["target_revenue"]],
        ["Qualified revenue", s["qualified_revenue"]],
        ["Disqualified revenue", s["disqualified_revenue"]],
        ["Achievement %", s["achievement_pct"]],
        ["Qualification %", s["qualification_pct"]],
        ["Incentive liability", s["incentive_liability"]],
        ["Net payable", s["net_payable"]],
    ])

    people_cols = ["employee_id", "full_name", "designation", "region", "zone",
                   "target_units", "achieved_units", "target_revenue", "qualified_revenue",
                   "disqualified_revenue", "base_pct", "total_incentive", "net_payable"]
    sheet(wb.create_sheet("People"), people_cols,
          [[e.get(c) for c in people_cols] for e in merged["employees"]])

    # One row per person per month, so a total can be traced to the month it came from.
    month_cols = ["period", "employee_id", "full_name", "region", "target_revenue",
                  "qualified_revenue", "disqualified_revenue", "base_pct", "total_incentive"]
    sheet(wb.create_sheet("By month"), month_cols,
          [[m] + [e.get(c) for c in month_cols[1:]]
           for m, d in data for e in d["employees"]
           if e.get("target_units") is not None or e.get("total_incentive") is not None])

    buf = io.BytesIO()
    wb.save(buf)
    audit.record(principal.email, "EXPORT", entity_type="report",
                 affected_record=f"range:{start}..{end}",
                 new_value={"rows": len(merged["employees"]), "format": "xlsx"})
    safe = label.replace(" ", "-").replace("·", "")
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="incentive-{safe}.xlsx"'},
    )


@router.get("/{report}")
def export_report(
    report: str,
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(require(Permission.EXPORT_SCOPED)),
):
    if report not in REPORTS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown report. Choose one of: {', '.join(REPORTS)}.",
        )
    _require_published(principal, period)
    # A BDE's scope predicate resolves to their own row, so a BDE calling the
    # company-wide export gets a one-row file rather than a 403.
    rows = dashboards.team_rows(principal, period)
    audit.record(
        principal.email, "EXPORT", entity_type="report",
        affected_record=f"{report}:{period}",
        new_value={"rows": len(rows)},
    )
    return _csv(rows, f"{report}-{period}.csv")
