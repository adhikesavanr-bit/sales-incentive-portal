"""Crash reports from the browser.

A client-side exception otherwise leaves no trace anywhere we can read: the
details sit in one person's browser console. The frontend posts them here and
they land in the Cloud Run log, next to the API's own errors.

Signed-in callers only, and every field is capped, so this cannot be used to
flood the log.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from app.auth.deps import current_principal
from app.auth.rbac import Principal

log = logging.getLogger("client_errors")
router = APIRouter(prefix="/api", tags=["client-errors"])


class ClientError(BaseModel):
    kind: str = Field("error", max_length=40)       # error / chunk / boundary
    message: str = Field("", max_length=1000)
    stack: str = Field("", max_length=4000)
    url: str = Field("", max_length=500)
    user_agent: str = Field("", max_length=300)
    digest: str | None = Field(None, max_length=100)


@router.post("/client-errors", status_code=status.HTTP_204_NO_CONTENT,
             response_class=Response)
def report_client_error(
    body: ClientError,
    principal: Principal = Depends(current_principal),
) -> Response:
    log.warning("CLIENT_ERROR %s", json.dumps({"user": principal.email, **body.model_dump()}))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
