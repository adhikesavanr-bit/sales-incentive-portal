"""Bulk target upload: parsing, per-row checks, and what gets written."""
import pytest

from app.auth.rbac import ROLE_PERMISSIONS, Principal
from app.models.schemas import Role
from app.services import targets

ADMIN = Principal(email="fin@marrowmed.com", employee_id="FIN1", full_name="Fin",
                  role=Role.FINANCE_ADMIN,
                  permissions=set(ROLE_PERMISSIONS[Role.FINANCE_ADMIN]))

PEOPLE = [
    {"employee_id": "NHP001", "full_name": "A", "region": "South",
     "target_units": 10.0, "winner_units": 13.0},
    {"employee_id": "NHP002", "full_name": "B", "region": "North",
     "target_units": None, "winner_units": None},
]


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(targets, "in_scope", lambda p, period: PEOPLE)


def test_parse_accepts_friendly_headings():
    csv = b"Employee ID,Name,Target Units\nNHP001,A,12\n,,\nNHP002,B,\n"
    rows = targets.parse("t.csv", csv)
    assert [(r["line"], r["employee_id"]) for r in rows] == [(2, "NHP001"), (3, "NHP002")]


def test_parse_rejects_a_file_without_the_columns():
    with pytest.raises(ValueError, match="employee_id"):
        targets.parse("t.csv", b"name,units_sold\nA,1\n")


def test_preview_flags_each_kind_of_row(scope):
    rows = targets.parse("t.csv", (
        b"employee_id,target_units\n"
        b"NHP001,10\n"      # same as now
        b"NHP002,20\n"      # new
        b"NHP999,5\n"       # not in scope
        b"NHP002,21\n"      # duplicate
        b"NHP001,abc\n"     # not a number
    ))
    out = targets.preview(ADMIN, "2026-09", rows)
    statuses = [(r["employee_id"], r["status"]) for r in out["rows"]]
    assert statuses == [("NHP001", "unchanged"), ("NHP002", "change"), ("NHP999", "error"),
                        ("NHP002", "error"), ("NHP001", "error")]
    assert out["rows"][1]["winner_units"] == pytest.approx(26.0)
    assert out["counts"] == {"change": 1, "unchanged": 1, "skipped": 0, "error": 3}


def test_commit_writes_only_changes_with_next_version(scope, monkeypatch):
    written, audited = [], []
    monkeypatch.setattr(targets.bq, "query",
                        lambda sql, params=None: [{"employee_id": "NHP001", "v": 3}])
    monkeypatch.setattr(targets.bq, "append_rows", lambda t, rows: written.extend(rows))
    monkeypatch.setattr(targets.audit, "record_many",
                        lambda who, action, entries, reason=None: audited.extend(entries))
    rows = targets.parse("t.csv", b"employee_id,target_units\nNHP001,15\nNHP002,\n")
    checked = targets.preview(ADMIN, "2026-09", rows)
    assert targets.commit(ADMIN, "2026-09", checked, "Q3 plan") == 1
    assert [(w["employee_id"], w["version"], w["status"]) for w in written] == [
        ("NHP001", 4, "APPROVED")]
    assert len(audited) == 1


def test_endpoint_previews_then_refuses_a_file_with_errors(scope, monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth.deps import current_principal
    from app.main import app
    from app.routers import admin

    monkeypatch.setattr(admin.month, "require_open", lambda p: None)
    app.dependency_overrides[current_principal] = lambda: ADMIN
    try:
        client = TestClient(app)
        files = {"file": ("t.csv", b"employee_id,target_units\nNHP002,20\nNHP999,1\n", "text/csv")}
        preview = client.post("/api/targets/bulk", data={"period": "2026-09", "confirm": "false"},
                              files=files)
        assert preview.status_code == 200
        assert preview.json()["counts"]["change"] == 1
        saved = client.post("/api/targets/bulk",
                            data={"period": "2026-09", "confirm": "true", "reason": "plan"},
                            files=files)
        assert saved.status_code == 400
        assert "Nothing was saved" in saved.json()["detail"]
    finally:
        app.dependency_overrides.clear()
