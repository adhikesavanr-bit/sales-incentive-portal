"""Exports. Always scoped: the same predicate that guards the dashboards."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse

from app.auth.deps import require
from app.auth.rbac import Permission, Principal
from app.routers.dashboards import _authorise_target
from app.services import audit, dashboards, statement_pdf
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


# Declared before /{report}, which would otherwise swallow "statement".
@router.get("/statement")
def export_statement(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    employee_id: str | None = None,
    principal: Principal = Depends(require(Permission.EXPORT_SCOPED)),
):
    """One person's monthly statement as a PDF: their own, or anyone in scope."""
    target = _authorise_target(principal, employee_id)
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
    # A BDE's scope predicate resolves to their own row, so a BDE calling the
    # company-wide export gets a one-row file rather than a 403.
    rows = dashboards.team_rows(principal, period)
    audit.record(
        principal.email, "EXPORT", entity_type="report",
        affected_record=f"{report}:{period}",
        new_value={"rows": len(rows)},
    )
    return _csv(rows, f"{report}-{period}.csv")
