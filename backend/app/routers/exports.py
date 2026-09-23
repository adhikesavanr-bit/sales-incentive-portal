"""Exports. Always scoped: the same predicate that guards the dashboards."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.auth.deps import require
from app.auth.rbac import Permission, Principal
from app.services import audit, dashboards

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
