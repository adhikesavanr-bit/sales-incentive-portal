"""Upload validation."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from app.services.upload import COLUMN_MAP, REQUIRED, read_file, to_transactions, validate

GOOD = {
    "payment_id": ["marrowmed-a-1", "marrowmed-b-1"],
    "payment_status": ["captured", "captured"],
    "payment_date_ist": ["2026-08-01 12:00:00", "2026-08-02 13:00:00"],
    "paid_amount": [35999.0, 18499.0],
    "paid_amount_*_100/118": [30507.63, 15677.12],
    "coupon": ["ANG26MME", "ILPFMAG26"],
}


def frame(**overrides) -> pd.DataFrame:
    data = {**GOOD, **overrides}
    return pd.DataFrame(data)


class TestFileReading:
    def test_reads_csv(self):
        buf = io.BytesIO(frame().to_csv(index=False).encode())
        df = read_file("sales.csv", buf.getvalue())
        assert len(df) == 2

    def test_reads_xlsx(self, tmp_path):
        p = tmp_path / "sales.xlsx"
        frame().to_excel(p, index=False)
        df = read_file("sales.xlsx", p.read_bytes())
        assert len(df) == 2


class TestValidation:
    def test_a_clean_file_has_no_errors(self):
        assert validate(frame()) == []

    def test_missing_required_column_stops_everything(self):
        df = frame().drop(columns=["coupon"])
        errors = validate(df)
        assert len(errors) == 1
        assert errors[0].error_code == "MISSING_COLUMN"
        assert "coupon" in errors[0].message

    @pytest.mark.parametrize("column", REQUIRED)
    def test_every_required_column_is_checked(self, column):
        errors = validate(frame().drop(columns=[column]))
        assert any(e.error_code == "MISSING_COLUMN" and e.field == column
                   for e in errors)

    def test_blank_payment_id_is_reported_with_its_row_number(self):
        errors = validate(frame(payment_id=["", "marrowmed-b-1"]))
        assert errors[0].error_code == "MISSING_PAYMENT_ID"
        assert errors[0].row_number == 2   # 1-based, header included

    def test_unreadable_date_is_reported(self):
        errors = validate(frame(payment_date_ist=[None, "2026-08-02 13:00:00"]))
        assert any(e.error_code == "INVALID_DATE" for e in errors)

    def test_non_numeric_amount_is_reported(self):
        errors = validate(frame(paid_amount=["thirty-six thousand", 18499.0]))
        assert any(e.error_code == "INVALID_AMOUNT" for e in errors)

    def test_partial_errors_do_not_reject_the_good_rows(self):
        errors = validate(frame(payment_id=["", "marrowmed-b-1"]))
        assert len({e.row_number for e in errors}) == 1


class TestColumnMapping:
    def test_gst_net_column_is_renamed_to_a_legal_identifier(self):
        assert COLUMN_MAP["paid_amount_*_100/118"] == "net_amount"
        assert COLUMN_MAP["IGST_18%_if_not_KA"] == "igst_amount"

    def test_every_source_column_maps_to_a_snake_case_name(self):
        for target in COLUMN_MAP.values():
            assert target == target.lower()
            assert all(c.isalnum() or c == "_" for c in target)

    def test_transactions_carry_gross_and_net_separately(self):
        txns = to_transactions(frame())
        assert txns[0].paid_amount == 35999.0
        assert txns[0].net_amount == 30507.63
        assert txns[0].coupon == "ANG26MME"


class TestSourceTableTemplate:
    """Sales arrive as one versioned table per month, so the name is derived."""

    def test_renders_the_default_pattern(self):
        from app.services.source_tables import render_template
        assert render_template("{MMM}_{YYYY}_v1", "2026-08") == "Aug_2026_v1"
        assert render_template("{MMM}_{YYYY}_v1", "2026-12") == "Dec_2026_v1"

    def test_other_placeholders(self):
        from app.services.source_tables import render_template
        assert render_template("sales_{YYYY}{MM}", "2026-08") == "sales_202608"
        assert render_template("{period}", "2026-08") == "2026-08"
        assert render_template("m{YY}{MM}", "2026-08") == "m2608"


class TestColumnMatching:
    """A table whose columns are named differently still connects."""

    def test_matches_the_supplied_extract(self):
        from app.services.source_tables import map_columns
        cols = ["payment_id", "payment_status", "payment_date_ist", "paid_amount",
                "paid_amount_*_100/118", "coupon", "college_id", "Plan_Title"]
        mapping, missing = map_columns(cols)
        assert missing == []
        assert mapping["net_amount"] == "paid_amount_*_100/118"
        assert mapping["plan_title"] == "Plan_Title"

    def test_ignores_case_spaces_and_underscores(self):
        from app.services.source_tables import map_columns
        cols = ["Payment ID", "Status", "Payment Date", "Amount",
                "Net Amount", "Coupon Code"]
        mapping, missing = map_columns(cols)
        assert missing == []
        assert mapping["payment_id"] == "Payment ID"
        assert mapping["coupon"] == "Coupon Code"

    def test_names_what_is_missing_rather_than_failing_vaguely(self):
        from app.services.source_tables import map_columns
        mapping, missing = map_columns(["payment_id", "coupon"])
        assert set(missing) == {"payment_status", "payment_date_ist",
                                "paid_amount", "net_amount"}

    def test_optional_columns_are_not_required(self):
        from app.services.source_tables import map_columns
        cols = ["payment_id", "payment_status", "payment_date_ist",
                "paid_amount", "net_amount", "coupon"]
        mapping, missing = map_columns(cols)
        assert missing == []
        assert "plan_title" not in mapping


class TestPeriodPredicate:
    """The month filter must prune partitions, and must use IST boundaries."""

    def test_timestamp_column_uses_a_range(self):
        from app.services.source_tables import period_predicate
        sql = period_predicate("payment_date_ist", "TIMESTAMP")
        assert "FORMAT_TIMESTAMP" not in sql      # would block partition pruning
        assert ">=" in sql and "<" in sql
        assert "Asia/Kolkata" in sql

    def test_range_is_half_open_so_months_do_not_overlap(self):
        from app.services.source_tables import period_predicate
        sql = period_predicate("payment_date_ist", "TIMESTAMP")
        assert "INTERVAL 1 MONTH" in sql
        assert "<=" not in sql

    def test_string_date_column_still_works(self):
        from app.services.source_tables import period_predicate
        sql = period_predicate("payment_date", "STRING")
        assert "FORMAT_TIMESTAMP" in sql

    def test_date_and_datetime_are_range_filtered(self):
        from app.services.source_tables import period_predicate
        for t in ("DATE", "DATETIME"):
            assert "FORMAT_TIMESTAMP" not in period_predicate("d", t)


class TestRealTableSchema:
    """The columns of Monthly_Sub.Aug_2026_v1, as supplied."""

    COLUMNS = [
        "order_id", "invoice_id", "payment_id", "payment_status", "payment_date",
        "subscription_date", "subscription_date_ist", "payment_date_ist",
        "is_upgrade", "paid_amount", "paid_amount___100_118", "IGST_18%_if_not_KA",
        "CGST_9%_if_KA", "SGST_9%_if_KA", "Duration_in_Days", "Destination_State",
        "currency", "payment_ref_id", "Plan_ID", "Plan_Title", "Plan_Description",
        "Plan_Duration_in_Month", "Addon_Plan_IDs", "Addon_Plans_Title", "user_id",
        "city", "state", "college_id", "college_name", "college_state",
        "year_of_admission", "coupon", "coupon_discount", "coupon_agent",
        "loyalty_discount_applied", "eoi", "termination_remark", "terminated_by",
        "termination_date", "course_id", "reference_id", "gift_plan_payment_ref_id",
    ]

    def test_every_required_column_resolves(self):
        from app.services.source_tables import map_columns
        mapping, missing = map_columns(self.COLUMNS)
        assert missing == []

    def test_the_gst_net_column_is_found_despite_its_name(self):
        """`paid_amount_*_100/118` in the extract, sanitised in BigQuery."""
        from app.services.source_tables import map_columns
        mapping, _ = map_columns(self.COLUMNS)
        assert mapping["net_amount"] == "paid_amount___100_118"

    def test_net_is_not_confused_with_gross(self):
        from app.services.source_tables import map_columns
        mapping, _ = map_columns(self.COLUMNS)
        assert mapping["paid_amount"] == "paid_amount"
        assert mapping["paid_amount"] != mapping["net_amount"]

    def test_ist_date_wins_over_the_utc_one(self):
        from app.services.source_tables import map_columns
        mapping, _ = map_columns(self.COLUMNS)
        assert mapping["payment_date_ist"] == "payment_date_ist"

    def test_all_optional_columns_are_present_too(self):
        from app.services.source_tables import map_columns, OPTIONAL_COLUMNS
        mapping, _ = map_columns(self.COLUMNS)
        assert set(OPTIONAL_COLUMNS) <= set(mapping)


class TestSqlStatementSplitting:
    """A semicolon inside a comment or a string is not a statement terminator."""

    def test_semicolon_in_a_line_comment_does_not_split(self):
        from app.db.ddl import split_statements
        sql = """
        CREATE TABLE a (
          x STRING,   -- BDE initial; null on ~43% of rows
          y STRING
        );
        """
        stmts = split_statements(sql)
        assert len(stmts) == 1
        assert stmts[0].count("(") == stmts[0].count(")")

    def test_semicolon_in_a_string_literal_does_not_split(self):
        from app.db.ddl import split_statements
        sql = "CREATE TABLE a (x STRING) OPTIONS (description = 'one; two'); SELECT 1;"
        assert len(split_statements(sql)) == 2

    def test_the_real_schema_parses_into_balanced_statements(self):
        import pathlib
        from app.db.ddl import split_statements
        sql = (pathlib.Path(__file__).resolve().parents[1] / "app/db/schema.sql").read_text()
        sql = sql.replace("${PROJECT}", "p").replace("${DATASET}", "d").replace("${LOCATION}", "US")
        stmts = split_statements(sql)
        assert len(stmts) == 20
        for s in stmts:
            assert s.count("(") == s.count(")"), s.splitlines()[0]

    def test_every_expected_table_is_created(self):
        import pathlib, re
        from app.db.ddl import split_statements
        sql = (pathlib.Path(__file__).resolve().parents[1] / "app/db/schema.sql").read_text()
        sql = sql.replace("${PROJECT}", "p").replace("${DATASET}", "d").replace("${LOCATION}", "US")
        created = set()
        for s in split_statements(sql):
            m = re.search(r"CREATE (?:TABLE IF NOT EXISTS|OR REPLACE VIEW) `p\.d\.(\w+)`", s)
            if m:
                created.add(m.group(1))
        assert {"raw_sales", "coupon_master", "employee_master", "reporting_hierarchy",
                "targets", "incentive_rules", "coupon_rules", "monthly_incentive",
                "month_status", "source_table_config", "audit_log",
                "v_employee_hierarchy", "v_incentive_current"} <= created

    def test_trailing_statement_without_a_semicolon_is_kept(self):
        from app.db.ddl import split_statements
        assert len(split_statements("SELECT 1; SELECT 2")) == 2


class TestSchemaPartitioning:
    """BigQuery only partitions on a real DATE or TIMESTAMP column."""

    @staticmethod
    def _statements():
        import pathlib
        from app.db.ddl import split_statements
        sql = (pathlib.Path(__file__).resolve().parents[1] / "app/db/schema.sql").read_text()
        return split_statements(
            sql.replace("${PROJECT}", "p").replace("${DATASET}", "d").replace("${LOCATION}", "US")
        )

    def test_no_statement_partitions_on_a_parsed_string(self):
        """PARTITION BY PARSE_DATE(...) over a STRING column is rejected by BigQuery."""
        for s in self._statements():
            if "PARTITION BY" in s:
                clause = next(l for l in s.splitlines() if "PARTITION BY" in l)
                assert "PARSE_DATE" not in clause, clause

    def test_partitioning_is_only_on_timestamp_columns(self):
        import re
        partitioned = {}
        for s in self._statements():
            if "PARTITION BY" in s:
                name = re.search(r"`p\.d\.(\w+)`", s).group(1)
                partitioned[name] = next(l.strip() for l in s.splitlines() if "PARTITION BY" in l)
        assert partitioned == {
            "raw_sales": "PARTITION BY DATE(payment_date_ist)",
            "audit_log": "PARTITION BY DATE(occurred_at)",
        }

    def test_period_keyed_tables_cluster_on_period_first(self):
        """Without partitioning, period must lead the clustering key to prune."""
        import re
        checked = set()
        for s in self._statements():
            # Only the CREATE TABLE statement, not the view that reads from it.
            # Statements begin with a comment block, so search rather than
            # match; the CREATE TABLE prefix is what excludes the view.
            m = re.search(
                r"CREATE TABLE IF NOT EXISTS `p\.d\.(transaction_qualification|monthly_incentive)`",
                s,
            )
            if m:
                clause = next(l.strip() for l in s.splitlines() if "CLUSTER BY" in l)
                assert clause.startswith("CLUSTER BY period,"), f"{m.group(1)}: {clause}"
                checked.add(m.group(1))
        assert checked == {"transaction_qualification", "monthly_incentive"}


class TestJsonColumns:
    """BigQuery JSON columns take a string on insert, not a Python object."""

    def test_column_map_is_serialised_before_insert(self, monkeypatch):
        import json
        from app.services import source_tables

        captured = {}
        monkeypatch.setattr(source_tables.bq, "query", lambda *a, **k: [])
        monkeypatch.setattr(
            source_tables.bq, "append_rows",
            lambda table, rows: captured.update(table=table, rows=rows),
        )
        source_tables.set_for_period(
            "2026-08", "Monthly_Sub", "Aug_2026_v1", "someone@example.com",
            column_map={"payment_id": "payment_id", "net_amount": "paid_amount___100_118"},
        )
        value = captured["rows"][0]["column_map"]
        assert isinstance(value, str)
        assert json.loads(value)["net_amount"] == "paid_amount___100_118"

    def test_absent_column_map_stays_null(self, monkeypatch):
        from app.services import source_tables

        captured = {}
        monkeypatch.setattr(source_tables.bq, "query", lambda *a, **k: [])
        monkeypatch.setattr(
            source_tables.bq, "append_rows",
            lambda table, rows: captured.update(rows=rows),
        )
        source_tables.set_for_period(
            "2026-08", "Monthly_Sub", "Aug_2026_v1", "someone@example.com"
        )
        assert captured["rows"][0]["column_map"] is None

    def test_a_json_column_read_back_as_a_string_is_parsed(self):
        from app.services.source_tables import _load_json
        assert _load_json('{"a": "b"}') == {"a": "b"}
        assert _load_json({"a": "b"}) == {"a": "b"}
        assert _load_json(None) is None


class TestSchemaMatchesModels:
    """Every field the code writes must exist as a column.

    A missing column fails only at load time, deep inside a BigQuery job, with
    a message that names one field and stops. Comparing the whole surface here
    catches the rest in one go.
    """

    @staticmethod
    def _columns():
        import pathlib, re
        from app.db.ddl import split_statements
        sql = (pathlib.Path(__file__).resolve().parents[1] / "app/db/schema.sql").read_text()
        sql = sql.replace("${PROJECT}", "p").replace("${DATASET}", "d").replace("${LOCATION}", "US")
        out = {}
        for s in split_statements(sql):
            m = re.search(r"CREATE TABLE IF NOT EXISTS `p\.d\.(\w+)`\s*\((.*?)\n\)", s, re.S)
            if m:
                names = []
                for line in m.group(2).splitlines():
                    line = line.split("--")[0].strip().rstrip(",")
                    if line:
                        names.append(line.split()[0])
                out[m.group(1)] = set(names)
        return out

    def test_signature_qualification_has_every_model_field(self):
        from app.models.schemas import SignatureQualification
        written = set(SignatureQualification.model_fields) | {"period", "calculated_at"}
        assert written <= self._columns()["signature_qualification"]

    def test_monthly_incentive_has_every_model_field(self):
        from app.models.schemas import IncentiveBreakdown
        written = (
            set(IncentiveBreakdown.model_fields)
            - {"slab_hits", "calculated_at", "arpu_threshold"}
        ) | {"slab_hits", "calculated_at", "calculated_by", "notes"}
        assert written <= self._columns()["monthly_incentive"]

    def test_transaction_qualification_has_every_written_field(self):
        written = {
            "period", "payment_id", "employee_id", "coupon_signature", "status",
            "disqualification_reason", "reason_detail", "is_foundation",
            "paid_amount", "net_amount", "calculation_version", "calculated_at",
        }
        assert written <= self._columns()["transaction_qualification"]

    def test_the_own_sales_floor_is_recorded(self):
        """Finance has to be able to see why a coupon was refused."""
        cols = self._columns()["signature_qualification"]
        assert {"min_own_sales", "own_sales_met"} <= cols


class TestNumericFitting:
    """BigQuery NUMERIC holds 9 decimal places; net-of-GST maths produces more."""

    def test_the_value_that_broke_the_load(self):
        from app.db.bigquery import _fit_numeric
        # 11499 * 100/118
        assert _fit_numeric(9744.915254237289) == 9744.915254237

    def test_rounding_is_lossless_at_any_scale_that_matters(self):
        from app.db.bigquery import _fit_numeric
        # a billionth of a rupee, against a crore
        assert abs(_fit_numeric(22414108.79123456789) - 22414108.79123456789) < 1e-8

    def test_nested_values_are_fitted(self):
        from app.db.bigquery import fit_rows
        rows = fit_rows([{
            "net_amount": 9744.915254237289,
            "slab_hits": [{"rate": 0.0225, "input_value": 1.2345678901234}],
            "notes": ["unchanged"],
        }])
        assert rows[0]["net_amount"] == 9744.915254237
        assert rows[0]["slab_hits"][0]["input_value"] == 1.234567890
        assert rows[0]["notes"] == ["unchanged"]

    def test_non_floats_are_untouched(self):
        from app.db.bigquery import _fit_numeric
        assert _fit_numeric("2026-08") == "2026-08"
        assert _fit_numeric(79) == 79
        assert _fit_numeric(True) is True
        assert _fit_numeric(None) is None


class TestSourceTableStandardNaming:
    """Monthly tables are named <Mmm>_<YYYY>; August predates the convention."""

    def test_the_standard_template(self):
        from app.services.source_tables import render_template
        for period, table in [
            ("2026-04", "Apr_2026"), ("2026-07", "Jul_2026"),
            ("2026-09", "Sep_2026"), ("2027-01", "Jan_2027"),
        ]:
            assert render_template("{MMM}_{YYYY}", period) == table

    def test_a_period_override_beats_the_template(self, monkeypatch):
        """August is Aug_2026_v1, so it carries an explicit config row."""
        from app.services import source_tables

        monkeypatch.setattr(source_tables.bq, "query", lambda *a, **k: [{
            "source_project": "p", "source_dataset": "Monthly_Sub",
            "source_table": "Aug_2026_v1", "date_column": "payment_date_ist",
            "column_map": None,
        }])
        src = source_tables.resolve("2026-08")
        assert src.table == "Aug_2026_v1"
        assert src.origin == "config"
