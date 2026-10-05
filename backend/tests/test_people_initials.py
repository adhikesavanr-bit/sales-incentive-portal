"""Coupon initials set in People, and the warning for targets with no sales.

Sep 2026: NHP706, NHP845 and NHP829 were added in People with initials ABJ,
AKG and PJH and given approved targets, but only the uploaded agent list was
read, so their 149 sales were left out and the run paid them nothing.
"""
from datetime import date, datetime

from app.models.schemas import Employee, IncentiveBreakdown, Role, SalesTransaction, Target
from app.services import coupon_upload as cu
from app.services import employees as employee_service
from app.services.incentive_run import unlinked_targets

from .conftest import sig

UPLOADED = {
    "AK": {"initial": "AK", "employee_id": "NHP001", "agent_name": "A K", "zone": "R1"},
    "URV": {"initial": "URV", "employee_id": "NHP689", "agent_name": None, "zone": "Inside Sales"},
}


def person(emp, initial, region="R8-Tauseef", active=True, exit_date=None):
    return {"employee_id": emp, "full_name": f"Person {emp}", "initial": initial,
            "region": region, "is_active": active, "exit_date": exit_date}


# --- People initials fill the agent list -------------------------------------
def test_a_people_initial_missing_from_the_list_is_added():
    agents, warnings, added = cu.merge_agents(UPLOADED, [person("NHP706", "abj")], "2026-09")
    assert agents["ABJ"] == {"initial": "ABJ", "employee_id": "NHP706",
                             "agent_name": "Person NHP706", "zone": "R8-Tauseef"}
    assert added == {"ABJ": "NHP706"} and warnings == []


def test_the_uploaded_list_wins_and_keeps_people_not_in_people():
    agents, warnings, added = cu.merge_agents(
        UPLOADED, [person("NHP001", "AK", region="R9"), person("NHP999", "AK")], "2026-09")
    assert agents["AK"]["employee_id"] == "NHP001" and agents["AK"]["zone"] == "R1"
    assert agents["URV"]["zone"] == "Inside Sales"          # Inside Sales stays non-field
    assert added == {}
    assert len(warnings) == 1 and "NHP999" in warnings[0] and "NHP001" in warnings[0]


def test_someone_who_left_before_the_month_is_not_added():
    agents, _, added = cu.merge_agents(
        UPLOADED, [person("NHP500", "OLD", active=False, exit_date=date(2026, 8, 15))], "2026-09")
    assert "OLD" not in agents and added == {}


def test_someone_who_left_during_the_month_still_owns_their_coupons():
    agents, _, added = cu.merge_agents(
        UPLOADED, [person("NHP500", "MID", active=False, exit_date=date(2026, 9, 20))], "2026-09")
    assert added == {"MID": "NHP500"}


def test_coupons_on_a_people_initial_are_built_and_flagged():
    agents, _, added = cu.merge_agents(UPLOADED, [person("NHP706", "ABJ")], "2026-09")
    header = ("coupon_signature,coupon,activation_date,deactivation_date,discount_group_id,"
              "sales_master,marrow_marvel_user_ids,sales_count,college_ids,payment_ref_ids\n")
    df = cu.parse_report("r.csv", (header +
        "ABJ01_x,ABJ01,26/09/02 10:00,26/09/09 18:29,1_5_FIELDG10,ABJ,,12,c1,p\n").encode())
    out = cu.build(df, agents, "2026-09", from_people=added)
    assert out["included"] == 1 and out["rows"][0]["employee_id"] == "NHP706"
    assert out["rows"][0]["region"] == "R8-Tauseef"
    assert any("ABJ go to NHP706" in w for w in out["warnings"])


def test_initials_are_stored_upper_case_and_blank_means_none():
    e = Employee(employee_id="NHP706", full_name="A", initial=" abj ")
    employee_service.normalise_initial(e)
    assert e.initial == "ABJ"
    e.initial = "  "
    employee_service.normalise_initial(e)
    assert e.initial is None


# --- the warning -------------------------------------------------------------
def tx(coupon_agent, n):
    return [SalesTransaction(payment_id=f"{coupon_agent}{i}", payment_status="captured",
                             payment_date_ist=datetime(2026, 9, 10), paid_amount=35999,
                             net_amount=30507.63, coupon=f"{coupon_agent}01",
                             coupon_agent=coupon_agent) for i in range(n)]


def setup():
    emps = {
        "NHP706": Employee(employee_id="NHP706", full_name="Abhijeet", initial="ABJ", role=Role.BDE),
        "NHP845": Employee(employee_id="NHP845", full_name="Akash", initial=None, role=Role.BDE),
        "NHP829": Employee(employee_id="NHP829", full_name="Prajwal", initial="PJH", role=Role.BDE),
        "NHP001": Employee(employee_id="NHP001", full_name="Seller", initial="AK", role=Role.BDE),
    }
    tg = {e: Target(employee_id=e, period="2026-09", target_units=u, winner_units=0)
          for e, u in [("NHP706", 65), ("NHP845", 50), ("NHP829", 40), ("NHP001", 30)]}
    bd = {e: IncentiveBreakdown(employee_id=e, period="2026-09") for e in tg}
    bd["NHP001"].gross_units = 12
    return emps, tg, bd


def test_each_person_with_a_target_and_no_linked_sales_is_named_with_the_cause():
    emps, tg, bd = setup()
    out = unlinked_targets("2026-09", bd, tg, emps, [sig("AK01", "NHP001", "c")],
                           tx("ABJ", 71) + tx("AK", 12), {"NHP001": {"AK"}})
    by = {w["employee_id"]: w for w in out}
    assert set(by) == {"NHP706", "NHP845", "NHP829"}          # NHP001 has sales
    assert by["NHP706"]["sales_on_their_codes"] == 71
    assert "71 sales this month are on ABJ coupons" in by["NHP706"]["reason"]
    assert "upload this month's coupon report again" in by["NHP706"]["reason"].lower()
    assert by["NHP845"]["reason"].startswith("No coupon initial")
    assert by["NHP829"]["reason"] == "No PJH coupons in this month's coupon report."


def test_an_agent_list_code_counts_even_without_a_people_initial():
    emps, tg, bd = setup()
    out = unlinked_targets("2026-09", bd, {"NHP845": tg["NHP845"]}, emps, [],
                           tx("AKG", 47), {"NHP845": {"AKG"}})
    assert out[0]["initial"] == "AKG" and out[0]["sales_on_their_codes"] == 47


def test_coupons_but_no_sales_says_so():
    emps, tg, bd = setup()
    out = unlinked_targets("2026-09", bd, {"NHP829": tg["NHP829"]}, emps,
                           [sig("PJH01", "NHP829", "c")], [], {})
    assert out[0]["reason"] == "1 PJH coupon(s) this month, but no sales on them."


def test_a_zero_target_is_not_flagged():
    emps, _, bd = setup()
    zero = {"NHP845": Target(employee_id="NHP845", period="2026-09", target_units=0, winner_units=0)}
    assert unlinked_targets("2026-09", bd, zero, emps, [], [], {}) == []
