"""Coupons from the consumption report, and the agent list behind them."""
import pytest

from app.services import coupon_upload as cu

AGENTS = {"AK": {"initial": "AK", "employee_id": "NHP001", "zone": "R1-Ajeet"}}
HEADER = ("coupon_signature,coupon,activation_date,deactivation_date,discount_group_id,"
          "sales_master,marrow_marvel_user_ids,sales_count,college_ids,payment_ref_ids\n")


def report(*lines: str) -> bytes:
    return (HEADER + "".join(l + "\n" for l in lines)).encode()


def build(content: bytes, period="2026-07"):
    return cu.build(cu.parse_report("r.csv", content), AGENTS, period)


def test_field_coupon_is_built_like_the_coupon_working():
    out = build(report("AK01_260701-12:20_1_5_FIELDG10,AK01,26/07/01 12:20,26/07/08 18:29,"
                       "1_5_FIELDG10,AK,,14,c1,p1"))
    (row,) = out["rows"]
    assert row == {
        "coupon_signature": "AK01_260701-12:20_1_5_FIELDG10", "coupon_code": "AK01",
        "employee_id": "NHP001", "initial": "AK", "region": "R1-Ajeet", "college_id": "c1",
        "discount_group": "1_5_FIELDG10", "group_size": "G10", "required_sales": 10,
        "min_sales": 8, "activation_date": "2026-07-01", "deactivation_date": "2026-07-08",
    }


def test_codes_not_in_the_agent_list_are_left_out():
    out = build(report("X_1,X1,26/07/01 10:00,26/07/08 18:29,2_5_FIELDG10,MKN,,3,c,p",
                       "X_2,X2,26/07/01 10:00,26/07/08 18:29,2_5_FIELDG10,MKN,,3,c,p"))
    assert out["included"] == 0 and out["excluded_by_code"] == {"MKN": 2}


def test_misspelt_group_is_still_read():
    out = build(report("S,S1,26/07/01 10:00,26/07/08 18:29,7_5_FILEDG15,AK,,3,c,p"))
    assert out["rows"][0]["group_size"] == "G15"


def test_duplicates_and_bad_dates_are_errors():
    out = build(report("S,S1,26/07/01 10:00,26/07/08 18:29,1_5_FIELDG5,AK,,3,c,p",
                       "S,S1,26/07/01 10:00,26/07/08 18:29,1_5_FIELDG5,AK,,3,c,p",
                       "T,T1,soon,26/07/08 18:29,1_5_FIELDG5,AK,,3,c,p"))
    assert [e["line"] for e in out["errors"]] == [3, 4]


def test_wrong_month_is_flagged():
    out = build(report("S,S1,26/06/01 10:00,26/06/08 18:29,1_5_FIELDG5,AK,,3,c,p"))
    assert "not 2026-07" in out["warnings"][0]


def test_a_sales_file_is_refused_with_a_clear_message():
    with pytest.raises(ValueError, match="coupon consumption report"):
        cu.parse_report("s.csv", b"order_id,payment_id,coupon\n1,2,3\n")


def test_agent_list_reads_the_workbook_headings():
    rows = cu.parse_agents("a.csv", b"BDE Initial,BDE Name,Employee IDs,Zone\nak,A K,NHP001,R1\n")
    assert rows == [{"initial": "AK", "employee_id": "NHP001", "agent_name": "A K", "zone": "R1"}]


def test_agent_list_refuses_one_code_for_two_people():
    with pytest.raises(ValueError, match="both"):
        cu.parse_agents("a.csv", b"BDE Initial,Employee IDs\nAK,NHP001\nAK,NHP002\n")


def test_a_month_reads_only_its_own_coupons(monkeypatch):
    from app.services import coupons

    seen = {}
    monkeypatch.setattr(coupons.bq, "query",
                        lambda sql, params=None: seen.update(sql=sql, params=params) or [])
    coupons.load_for_period("2026-08")
    assert "DATE_TRUNC(activation_date, MONTH) = PARSE_DATE('%Y-%m', @p)" in seen["sql"]
    assert "<=" not in seen["sql"]
    assert seen["params"] == {"p": "2026-08"}
