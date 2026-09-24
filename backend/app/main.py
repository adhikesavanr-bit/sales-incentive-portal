"""Sales & Incentive Management Portal — API entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.routers import admin, auth, client_errors, dashboards, exports, sales

settings = get_settings()
logging.basicConfig(level=settings.log_level)
log = logging.getLogger(__name__)

app = FastAPI(
    title="Sales & Incentive Management Portal",
    version="1.0.0",
    description=(
        "Field-sales incentive calculation for Marrow. Business logic is "
        "reproduced from the approved incentive workbook; see DATA_MAPPING.md."
    ),
)

async def catch_errors(request: Request, call_next):
    """Turn an unhandled error into a JSON 500 *inside* the CORS layer.

    FastAPI's own exception handler runs above the CORS middleware, so its
    response carries no Access-Control-Allow-Origin header and the browser
    reports a bare "Failed to fetch" — the one message that tells nobody
    anything. Handling it here means the real message reaches the UI.
    """
    try:
        return await call_next(request)
    except Exception:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Something went wrong on our side. "
                          "The error has been logged — try again, or contact Finance."
            },
        )


# Order matters: middleware added last is outermost, so CORS must come after
# the error handler in order to wrap it.
app.add_middleware(BaseHTTPMiddleware, dispatch=catch_errors)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, dashboards.router, sales.router, admin.router, exports.router,
          client_errors.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Never fail silently, and never leak a stack trace to the browser."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Something went wrong on our side. "
                      "The error has been logged — try again, or contact Finance."
        },
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    """Readiness probe: proves BigQuery is reachable with the mounted identity."""
    from app.db import bigquery as bq

    bq.query("SELECT 1 AS ok")
    return {"status": "ready", "dataset": settings.bq_dataset}
