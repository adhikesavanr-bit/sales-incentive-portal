"""Sales upload and validation. Finance only."""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.auth.deps import require
from app.auth.rbac import Permission, Principal
from app.services import audit, coupon_upload, month, upload

router = APIRouter(prefix="/api/sales", tags=["sales"])

# Preview results are held per batch so `import` does not re-parse the file.
# In a multi-instance deployment this moves to GCS (see GCS_UPLOAD_BUCKET).
_PENDING: dict[str, tuple] = {}


@router.post("/validate")
async def validate_upload(
    file: UploadFile = File(...),
    period: str = Form(..., pattern=r"^\d{4}-\d{2}$"),
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    """Parse, validate and dry-run qualification. Writes nothing."""
    month.require_open(period)

    if not file.filename or not file.filename.lower().endswith(
        (".csv", ".xlsx", ".xlsm", ".xls")
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload a .csv or .xlsx file.",
        )

    content = await file.read()
    try:
        summary, clean_df, _ = upload.preview(
            file.filename, content, period, principal.email
        )
    except Exception as exc:  # parse failures must not 500 silently
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The file could not be read: {exc}",
        ) from exc

    _PENDING[summary.batch_id] = (summary, clean_df, period)
    audit.record(
        principal.email, "SALES_VALIDATE",
        entity_type="upload_batch", affected_record=summary.batch_id,
        new_value=summary.model_dump(exclude={"errors"}),
    )
    return summary


@router.get("/validate/{batch_id}/errors.csv")
def download_errors(
    batch_id: str,
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    pending = _PENDING.get(batch_id)
    if pending is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That validation run has expired.")
    summary = pending[0]
    buf = io.StringIO()
    buf.write("row_number,payment_id,field,error_code,message\n")
    for e in summary.errors:
        buf.write(
            f"{e['row_number']},{e.get('payment_id') or ''},{e['field']},"
            f"{e['error_code']},\"{e['message']}\"\n"
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{batch_id}-errors.csv"'},
    )


@router.post("/import/{batch_id}")
def commit_upload(
    batch_id: str,
    confirm_with_errors: bool = False,
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    """Load a validated batch into raw_sales."""
    pending = _PENDING.get(batch_id)
    if pending is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "That validation run has expired. Validate the file again.",
        )
    summary, df, period = pending
    month.require_open(period)

    if summary.validation_status == "FAILED":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This file failed validation and cannot be imported.",
        )
    if summary.invalid_rows and not confirm_with_errors:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{summary.invalid_rows} rows have errors. "
            "Download the error report, or re-send with confirm_with_errors=true "
            "to import the valid rows only.",
        )

    rows = upload.commit(summary, df, period)
    _PENDING.pop(batch_id, None)
    audit.record(
        principal.email, "SALES_IMPORT",
        entity_type="upload_batch", affected_record=batch_id,
        new_value={"rows_imported": rows, "period": period},
    )
    return {"batch_id": batch_id, "rows_imported": rows, "period": period}


# --- coupons for the month ---------------------------------------------------
_FILE_TYPES = (".csv", ".xlsx", ".xlsm", ".xls")


async def _read(file: UploadFile) -> bytes:
    if not file.filename or not file.filename.lower().endswith(_FILE_TYPES):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a .csv or .xlsx file.")
    return await file.read()


def _preview(built: dict) -> dict:
    """The build without its rows: a summary, plus a few rows to eyeball."""
    return {**{k: v for k, v in built.items() if k != "rows"}, "sample": built["rows"][:5]}


@router.post("/coupons")
async def import_coupons(
    file: UploadFile = File(...),
    period: str = Form(..., pattern=r"^\d{4}-\d{2}$"),
    confirm: bool = Form(False),
    reason: str = Form(""),
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    """The month's coupons from the coupon consumption report.

    Without `confirm` it only checks the file. With it, the same file is
    checked again and saved, replacing any earlier upload for the month. A
    file with any error saves nothing.
    """
    month.require_open(period)
    content = await _read(file)
    agents, agent_warnings, from_people = coupon_upload.agents_for_upload(period)
    if not agents:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload the coupon agent list first: it decides which coupons are field coupons.",
        )
    try:
        built = coupon_upload.build(coupon_upload.parse_report(file.filename, content), agents,
                                    period, from_people=from_people)
        built["warnings"] = agent_warnings + built["warnings"]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - an unreadable file is the caller's to fix
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The file could not be read: {exc}") from exc
    if not confirm:
        return _preview(built)
    if built["errors"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Fix the rows with errors and upload again. Nothing was saved.")
    if len(reason.strip()) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give a reason for the upload.")
    written = coupon_upload.commit(built, period, principal.email, reason.strip())
    return {**_preview(built), "written": written}


@router.get("/coupon-agents")
def coupon_agents(principal: Principal = Depends(require(Permission.UPLOAD_SALES))):
    return coupon_upload.agents_summary()


@router.post("/coupon-agents")
async def replace_coupon_agents(
    file: UploadFile = File(...),
    confirm: bool = Form(False),
    reason: str = Form(""),
    principal: Principal = Depends(require(Permission.UPLOAD_SALES)),
):
    """Replace the coupon agent list (Coupon_Agent Details). Checked first."""
    content = await _read(file)
    try:
        rows = coupon_upload.parse_agents(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The file could not be read: {exc}") from exc
    current = coupon_upload.load_agents()
    new = {r["initial"]: r for r in rows}
    changed = sorted(i for i in new.keys() & current.keys()
                     if (new[i]["employee_id"], new[i]["zone"])
                     != (current[i]["employee_id"], current[i]["zone"]))
    diff = {
        "agents": len(rows),
        "added": sorted(new.keys() - current.keys()),
        "removed": sorted(current.keys() - new.keys()),
        "changed": changed,
    }
    if not confirm:
        return diff
    if len(reason.strip()) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give a reason for the change.")
    coupon_upload.replace_agents(rows, principal.email, reason.strip())
    return {**diff, "written": len(rows)}
