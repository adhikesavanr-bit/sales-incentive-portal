"""The write path for tables that are edited in place."""
from __future__ import annotations

from google.cloud import bigquery

from app.db import bigquery as bq


class _FakeClient:
    def __init__(self):
        self.loaded = None
        self.config = None

    def get_table(self, table_id):
        class T:
            schema = [
                bigquery.SchemaField("employee_id", "STRING"),
                bigquery.SchemaField("column_map", "JSON"),
                bigquery.SchemaField("amount", "NUMERIC"),
            ]
        return T()

    def load_table_from_json(self, rows, table_id, job_config):
        self.loaded, self.config = rows, job_config

        class Job:
            def result(self):
                return None
        return Job()

    def insert_rows_json(self, *a, **k):  # pragma: no cover - must not be used
        raise AssertionError("append_rows must not stream: streamed rows cannot be updated")


def _run(monkeypatch, rows):
    client = _FakeClient()
    monkeypatch.setattr(bq, "get_client", lambda: client)
    bq.append_rows("employee_master", rows)
    return client


def test_uses_a_load_job_with_the_table_schema(monkeypatch):
    c = _run(monkeypatch, [{"employee_id": "NHP001", "amount": 1.0}])
    assert c.config.write_disposition == "WRITE_APPEND"
    assert [f.name for f in c.config.schema] == ["employee_id", "column_map", "amount"]


def test_json_strings_are_loaded_as_objects(monkeypatch):
    c = _run(monkeypatch, [{"employee_id": "NHP001", "column_map": '{"a": "b"}'}])
    assert c.loaded[0]["column_map"] == {"a": "b"}


def test_absent_json_stays_sql_null(monkeypatch):
    c = _run(monkeypatch, [{"employee_id": "NHP001", "column_map": None}])
    assert "column_map" not in c.loaded[0]


def test_numerics_are_still_rounded(monkeypatch):
    c = _run(monkeypatch, [{"employee_id": "NHP001", "amount": 9744.915254237289}])
    assert c.loaded[0]["amount"] == round(9744.915254237289, 9)


def test_no_rows_is_a_no_op(monkeypatch):
    c = _run(monkeypatch, [])
    assert c.loaded is None
