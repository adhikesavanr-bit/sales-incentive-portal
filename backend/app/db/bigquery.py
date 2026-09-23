"""BigQuery access layer.

Credentials never leave the backend: the client is built from Application
Default Credentials (Workload Identity on Cloud Run) or from the service-account
file named by GOOGLE_APPLICATION_CREDENTIALS. Neither is ever serialised into a
response.

Every query is parameterised. No user input is ever formatted into SQL text.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Iterable, Sequence

from google.cloud import bigquery

from app.config import get_settings

log = logging.getLogger(__name__)

# BigQuery NUMERIC is precision 38, scale 9. Dividing by 118 to strip GST
# produces repeating decimals well past that — 9744.915254237289 has twelve —
# and the load fails rather than rounding. Nine decimal places is a
# billionth of a rupee, so this is lossless for anything the business cares
# about while still being a real rounding decision rather than an accident.
NUMERIC_SCALE = 9


def _fit_numeric(value):
    """Round floats to what NUMERIC can hold, recursively through containers."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, NUMERIC_SCALE)
    if isinstance(value, dict):
        return {k: _fit_numeric(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_fit_numeric(v) for v in value]
    return value


def fit_rows(rows):
    return [_fit_numeric(r) for r in rows]

_PY_TO_BQ = {
    str: "STRING",
    int: "INT64",
    float: "FLOAT64",
    bool: "BOOL",
}


@lru_cache
def get_client() -> bigquery.Client:
    s = get_settings()
    return bigquery.Client(project=s.gcp_project_id, location=s.bq_location)


def _to_param(name: str, value: Any) -> bigquery.query._AbstractQueryParameter:
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        bq_type = _PY_TO_BQ.get(type(items[0]), "STRING") if items else "STRING"
        return bigquery.ArrayQueryParameter(name, bq_type, items)
    return bigquery.ScalarQueryParameter(
        name, _PY_TO_BQ.get(type(value), "STRING"), value
    )


def query(sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Run a parameterised query and return plain dicts."""
    job_config = bigquery.QueryJobConfig(
        query_parameters=[_to_param(k, v) for k, v in (params or {}).items()],
        use_query_cache=True,
    )
    log.debug("bq query: %s", sql.strip().splitlines()[0] if sql.strip() else "")
    job = get_client().query(sql, job_config=job_config)
    return [dict(row) for row in job.result()]


def insert_rows(table: str, rows: Sequence[dict]) -> None:
    """Streaming insert. Used for audit and small control tables."""
    if not rows:
        return
    s = get_settings()
    table_id = f"{s.gcp_project_id}.{s.bq_dataset}.{table}"
    errors = get_client().insert_rows_json(table_id, fit_rows(rows))
    if errors:
        raise RuntimeError(f"BigQuery insert into {table} failed: {errors}")


def load_rows(table: str, rows: Iterable[dict], write_disposition: str = "WRITE_APPEND") -> int:
    """Batch load. Used for bulk sales imports — far cheaper than streaming."""
    rows = list(rows)
    if not rows:
        return 0
    s = get_settings()
    table_id = f"{s.gcp_project_id}.{s.bq_dataset}.{table}"
    job = get_client().load_table_from_json(
        fit_rows(rows),
        table_id,
        job_config=bigquery.LoadJobConfig(
            write_disposition=write_disposition,
            schema_update_options=[],
        ),
    )
    job.result()
    return len(rows)


def delete_where(table: str, where: str, params: dict[str, Any]) -> None:
    """Targeted delete, used when reprocessing a period before reimport."""
    s = get_settings()
    query(f"DELETE FROM {s.table(table)} WHERE {where}", params)
