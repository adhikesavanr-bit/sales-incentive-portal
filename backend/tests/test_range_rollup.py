"""Quarter / FY roll-ups add the months up and recompute ratios from the sums."""
from app.routers import dashboards as dash
from app.services import dashboards


def emp(eid, region, tu, au, tr, qr, inc, active=True):
    return {"employee_id": eid, "full_name": eid, "designation": "BDE", "region": region,
            "zone": "Z", "submanager_id": None, "rm_id": None, "target_units": tu,
            "achieved_units": au, "target_revenue": tr, "qualified_revenue": qr,
            "disqualified_revenue": 0, "total_incentive": inc, "net_payable": inc,
            "accumulation": 0, "is_active": active}


def test_sums_and_recomputes_ratios():
    apr = {"summary": {"target_revenue": 100, "qualified_revenue": 50}, "groups": None,
           "employees": [emp("A", "North", 10, 5, 100, 50, 10)]}
    may = {"summary": {"target_revenue": 100, "qualified_revenue": 150}, "groups": None,
           "employees": [emp("A", "South", 10, 15, 100, 150, 30)]}
    out = dashboards.merge_months([apr, may])
    a = out["employees"][0]
    assert a["target_revenue"] == 200 and a["qualified_revenue"] == 200
    assert a["total_incentive"] == 40
    assert a["region"] == "South"            # latest month's region
    assert a["base_pct"] == 1.0              # 200/200 and 20/20, not mean(0.5, 1.5)
    assert out["summary"]["achievement_pct"] == 1.0


def test_months_between_and_limits():
    assert dash._months_between("2026-04", "2026-06") == ["2026-04", "2026-05", "2026-06"]
    assert len(dash._months_between("2026-04", "2027-03")) == 12
    assert dash._months_between("2026-12", "2027-01") == ["2026-12", "2027-01"]


import pytest
from fastapi import HTTPException

from app.auth.rbac import ROLE_PERMISSIONS, Principal
from app.models.schemas import Role


def _who(role):
    return Principal(email="x@x.com", employee_id="E1", full_name="X", role=role,
                     permissions=set(ROLE_PERMISSIONS[role]))


@pytest.mark.parametrize("role", [Role.BDE, Role.SUB_MANAGER, Role.REGIONAL_MANAGER,
                                  Role.ZONAL_MANAGER, Role.TEAM_ADMIN])
def test_ranges_are_closed_to_everyone_but_bh_and_finance(role):
    with pytest.raises(HTTPException) as e:
        dash.rollup_range(start="2026-04", end="2026-06", group_by=None, principal=_who(role))
    assert e.value.status_code == 403
    if Role is not None and "VIEW_TEAM" in {p.value for p in ROLE_PERMISSIONS[role]}:
        with pytest.raises(HTTPException) as e:
            dash.my_consolidated_range(start="2026-04", end="2026-06", principal=_who(role))
        assert e.value.status_code == 403


def test_consolidated_merge_recomputes_ratios():
    base = {c: 0 for c in dashboards._SUMMED}
    a = {**base, "people": 5, "headcount": 4, "target_revenue": 100, "qualified_revenue": 50,
         "disqualified_revenue": 0, "total_incentive": 5, "accumulation": 1}
    b = {**base, "people": 6, "headcount": 5, "target_revenue": 100, "qualified_revenue": 150,
         "disqualified_revenue": 0, "total_incentive": 15, "accumulation": 2}
    out = dashboards.merge_consolidated([a, b])
    assert out["revenue_pct"] == 1.0 and out["total_incentive"] == 20
    assert out["people"] == 6 and out["accumulation"] == 2
    assert out["payout_pct"] == 0.1


def test_range_export_is_a_workbook_and_closed_to_other_roles(monkeypatch):
    import io
    from openpyxl import load_workbook
    from app.routers import exports
    row = emp("A", "North", 10, 5, 100, 50, 10)
    monkeypatch.setattr(exports, "can_see_month", lambda p, m: True)
    monkeypatch.setattr(exports, "load_range_months", lambda p, ms, g=None: [
        (m, {"summary": {"target_revenue": 100, "qualified_revenue": 50},
             "employees": [dict(row)], "groups": None}) for m in ms])
    monkeypatch.setattr(exports.audit, "record", lambda *a, **k: None)
    r = exports.export_range(start="2026-04", end="2026-06", label="Q1 AMJ 26-27",
                             principal=_who(Role.BUSINESS_HEAD))
    wb = load_workbook(io.BytesIO(r.body))
    assert wb.sheetnames == ["Summary", "People", "By month"]
    assert wb["People"].max_row == 2 and wb["By month"].max_row == 4  # 3 months + header
    assert wb["People"].cell(2, 8).value == 300                        # 3 months x 100 summed
    with pytest.raises(HTTPException) as e:
        exports.export_range(start="2026-04", end="2026-06", label="x",
                             principal=_who(Role.REGIONAL_MANAGER))
    assert e.value.status_code == 403
