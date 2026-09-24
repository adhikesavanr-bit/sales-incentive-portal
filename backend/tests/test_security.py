"""Access control: role permissions and scope predicates.

The threat these tests exist for: a BDE editing `employee_id=` in a URL or an
API call and receiving someone else's numbers.
"""
from __future__ import annotations

import pytest

from app.auth.rbac import (
    Permission,
    Principal,
    ROLE_PERMISSIONS,
    has_permission,
    visible_employee_sql,
)
from app.models.schemas import Role


def principal(role: Role, employee_id="NHP001", **kw) -> Principal:
    return Principal(
        email=f"{employee_id.lower()}@marrowmed.com",
        employee_id=employee_id,
        full_name="Test",
        role=role,
        permissions=set(ROLE_PERMISSIONS[role]),
        **kw,
    )


class TestPermissionMatrix:
    """The matrix from the brief, asserted row by row."""

    @pytest.mark.parametrize("role", list(Role))
    def test_everyone_sees_their_own_dashboard(self, role):
        assert has_permission(role, Permission.VIEW_OWN)

    @pytest.mark.parametrize("role,expected", [
        (Role.BDE, False), (Role.SUB_MANAGER, True), (Role.REGIONAL_MANAGER, True),
        (Role.ZONAL_MANAGER, True), (Role.BUSINESS_HEAD, True), (Role.FINANCE_ADMIN, True),
    ])
    def test_team_data(self, role, expected):
        assert has_permission(role, Permission.VIEW_TEAM) is expected

    @pytest.mark.parametrize("role,expected", [
        (Role.BDE, False), (Role.SUB_MANAGER, False), (Role.REGIONAL_MANAGER, True),
        (Role.ZONAL_MANAGER, True), (Role.BUSINESS_HEAD, True), (Role.FINANCE_ADMIN, True),
    ])
    def test_region_data(self, role, expected):
        assert has_permission(role, Permission.VIEW_REGION) is expected

    @pytest.mark.parametrize("role,expected", [
        (Role.BDE, False), (Role.REGIONAL_MANAGER, False), (Role.ZONAL_MANAGER, True),
        (Role.BUSINESS_HEAD, True), (Role.FINANCE_ADMIN, True),
    ])
    def test_zone_data(self, role, expected):
        assert has_permission(role, Permission.VIEW_ZONE) is expected

    @pytest.mark.parametrize("role", [
        Role.BDE, Role.SUB_MANAGER, Role.REGIONAL_MANAGER,
        Role.ZONAL_MANAGER, Role.BUSINESS_HEAD,
    ])
    def test_only_finance_uploads_sales(self, role):
        assert not has_permission(role, Permission.UPLOAD_SALES)
        assert has_permission(Role.FINANCE_ADMIN, Permission.UPLOAD_SALES)

    @pytest.mark.parametrize("perm", [
        Permission.MANAGE_EMPLOYEES, Permission.MANAGE_RULES,
        Permission.RECALCULATE, Permission.LOCK_MONTH,
    ])
    def test_managers_cannot_administer(self, perm):
        assert not has_permission(Role.ZONAL_MANAGER, perm)
        assert has_permission(Role.FINANCE_ADMIN, perm)

    def test_only_super_admin_reopens_a_locked_month(self):
        assert has_permission(Role.SUPER_ADMIN, Permission.REOPEN_MONTH)
        assert not has_permission(Role.FINANCE_ADMIN, Permission.REOPEN_MONTH)

    def test_a_bde_cannot_approve_its_own_target(self):
        assert not has_permission(Role.BDE, Permission.APPROVE_TARGETS)
        assert not has_permission(Role.BDE, Permission.PROPOSE_TARGETS)


class TestScopePredicate:
    """The predicate is built from the principal only — no request input."""

    def test_bde_scope_is_a_single_employee(self):
        sql, params = visible_employee_sql(principal(Role.BDE))
        assert sql == "h.employee_id = @scope_id"
        assert params == {"scope_id": "NHP001"}

    def test_submanager_scope_is_their_direct_reports_plus_self(self):
        sql, params = visible_employee_sql(principal(Role.SUB_MANAGER, "NHP002"))
        assert "h.submanager_id = @scope_id" in sql
        assert params["scope_id"] == "NHP002"

    def test_rm_scope_keys_on_rm_id(self):
        sql, _ = visible_employee_sql(principal(Role.REGIONAL_MANAGER, "NHP003"))
        assert "h.rm_id = @scope_id" in sql
        assert "h.zm_id" not in sql

    def test_zm_scope_keys_on_zm_id(self):
        sql, _ = visible_employee_sql(principal(Role.ZONAL_MANAGER, "NHP004"))
        assert "h.zm_id = @scope_id" in sql

    def test_business_head_is_bounded_by_vertical(self):
        sql, params = visible_employee_sql(
            principal(Role.BUSINESS_HEAD, "NHP005", vertical="Marrow")
        )
        assert sql == "h.vertical = @scope_vertical"
        assert params == {"scope_vertical": "Marrow"}

    def test_finance_sees_everything(self):
        sql, params = visible_employee_sql(principal(Role.FINANCE_ADMIN, "NHP999"))
        assert sql == "TRUE" and params == {}

    @pytest.mark.parametrize("role", list(Role))
    def test_predicate_never_embeds_a_literal(self, role):
        """Every value is a bound parameter, so scope cannot be SQL-injected."""
        sql, params = visible_employee_sql(principal(role, vertical="Marrow"))
        assert "'" not in sql and '"' not in sql
        for key in params:
            assert f"@{key}" in sql or sql == "TRUE"

    def test_a_bde_cannot_widen_scope_by_claiming_a_region(self):
        """Extra attributes on the principal do not grant reach."""
        p = principal(Role.BDE, region="R1", zone="Z1", vertical="Marrow")
        sql, params = visible_employee_sql(p)
        assert sql == "h.employee_id = @scope_id"
        assert "region" not in sql and "vertical" not in sql


class TestScopeEnforcement:
    """`is_in_scope` is the gate the employee-id route parameter passes through."""

    def test_out_of_scope_lookup_produces_a_bounded_query(self, monkeypatch):
        captured = {}

        def fake_query(sql, params=None):
            captured["sql"], captured["params"] = sql, params or {}
            return []

        from app.services import employees as svc
        monkeypatch.setattr(svc.bq, "query", fake_query)

        p = principal(Role.BDE)
        sql, params = visible_employee_sql(p)
        assert svc.is_in_scope("NHP002", sql, params) is False
        # the caller's own id constrains the query; the requested id is bound
        assert captured["params"]["scope_id"] == "NHP001"
        assert captured["params"]["target"] == "NHP002"
        assert "@target" in captured["sql"]

    def test_in_scope_lookup_returns_true(self, monkeypatch):
        from app.services import employees as svc
        monkeypatch.setattr(svc.bq, "query", lambda sql, params=None: [{"1": 1}])
        p = principal(Role.REGIONAL_MANAGER, "NHP003")
        sql, params = visible_employee_sql(p)
        assert svc.is_in_scope("NHP001", sql, params) is True


class TestDevLogin:
    """The local sign-in must be unreachable anywhere but a developer machine.

    These call the endpoint function directly with a stub request rather than
    through TestClient, because faking the caller's address is the whole point
    of the test and TestClient's support for that varies by version.
    """

    @staticmethod
    def _request(host="127.0.0.1"):
        class _Client:
            def __init__(self, h):
                self.host = h

        class _Request:
            def __init__(self, h):
                self.client = _Client(h) if h else None

        return _Request(host)

    @staticmethod
    def _setup(monkeypatch, *, allow, email="a@marrowmed.com", in_master=True):
        from app.config import get_settings
        from app.models.schemas import Employee
        from app.routers import auth as auth_router

        get_settings.cache_clear()
        monkeypatch.setenv("ALLOW_DEV_LOGIN", "true" if allow else "false")
        # Set it empty rather than deleting it. Deleting only removes the
        # environment variable; pydantic-settings then falls back to the
        # developer's own .env, so the test would pass or fail depending on
        # whose machine it ran on. An explicit empty value overrides both.
        monkeypatch.setenv("DEV_LOGIN_EMAIL", email or "")
        monkeypatch.setattr(
            auth_router.employee_service, "get_by_email",
            lambda e: Employee(employee_id="NHP001", full_name="A") if in_master else None,
        )
        monkeypatch.setattr(auth_router.audit, "record", lambda *a, **k: None)
        return auth_router

    def test_disabled_by_default_the_route_404s(self, monkeypatch):
        from fastapi import HTTPException
        r = self._setup(monkeypatch, allow=False)
        with pytest.raises(HTTPException) as exc:
            r.dev_login(self._request())
        assert exc.value.status_code == 404

    def test_enabled_it_issues_a_token(self, monkeypatch):
        r = self._setup(monkeypatch, allow=True)
        assert r.dev_login(self._request()).access_token

    def test_refused_from_a_non_loopback_address(self, monkeypatch):
        from fastapi import HTTPException
        r = self._setup(monkeypatch, allow=True)
        with pytest.raises(HTTPException) as exc:
            r.dev_login(self._request("10.0.0.7"))
        assert exc.value.status_code == 403

    def test_refused_for_an_address_not_in_the_employee_master(self, monkeypatch):
        from fastapi import HTTPException
        r = self._setup(monkeypatch, allow=True, in_master=False)
        with pytest.raises(HTTPException) as exc:
            r.dev_login(self._request())
        assert exc.value.status_code == 403

    def test_enabled_without_an_email_is_still_closed(self, monkeypatch):
        """ALLOW_DEV_LOGIN alone opens nothing — an address is also required."""
        from fastapi import HTTPException
        r = self._setup(monkeypatch, allow=True, email="")
        with pytest.raises(HTTPException) as exc:
            r.dev_login(self._request())
        assert exc.value.status_code == 404

    def test_ipv6_loopback_is_accepted(self, monkeypatch):
        r = self._setup(monkeypatch, allow=True)
        assert r.dev_login(self._request("::1")).access_token


class TestDashboardQueryQualification:
    """Columns shared by both sides of the join must be table-qualified.

    v_employee_hierarchy and monthly_incentive both carry employee_id,
    is_active, region, zone and designation. A bare reference is an ambiguous
    column error that only appears at runtime, against real BigQuery.
    """

    SHARED = ["is_active", "region", "zone", "designation"]

    def test_metrics_block_qualifies_every_shared_column(self):
        import re
        from app.services.dashboards import _METRICS
        for col in self.SHARED:
            for hit in re.finditer(rf"(?<![.\w]){col}\b", _METRICS):
                start = max(0, hit.start() - 2)
                assert _METRICS[start:hit.start()] in ("m.", "h."), (
                    f"{col} is unqualified in _METRICS"
                )

    def test_headcount_counts_the_incentive_row_not_the_employee_record(self):
        """An employee can be active in the master but have no sales this month."""
        from app.services.dashboards import _METRICS
        assert "m.is_active" in _METRICS
        assert "h.is_active" not in _METRICS


class TestEmptyDashboardState:
    """An absent row means two different things; the message must say which."""

    @staticmethod
    def _state(monkeypatch, status):
        from app.models.schemas import MonthStatus
        from app.routers import dashboards as router
        monkeypatch.setattr(router.month, "get_status", lambda p: status)
        return router._empty_state("NHP001", "2026-08")

    def test_an_open_month_reads_as_not_published(self, monkeypatch):
        from app.models.schemas import MonthStatus
        out = self._state(monkeypatch, MonthStatus.OPEN)
        assert out["status"] == "NOT_CALCULATED"
        assert out["message"] == "This month has not been published yet."

    def test_a_locked_month_reads_as_no_records(self, monkeypatch):
        from app.models.schemas import MonthStatus
        out = self._state(monkeypatch, MonthStatus.LOCKED)
        assert out["status"] == "NO_SALES"
        assert out["message"] == "No records for this month."
        assert out["month_status"] == "LOCKED"

    def test_every_published_status_reports_no_sales(self, monkeypatch):
        from app.models.schemas import MonthStatus
        for st in (MonthStatus.UNDER_REVIEW, MonthStatus.APPROVED, MonthStatus.LOCKED):
            assert self._state(monkeypatch, st)["status"] == "NO_SALES"

    def test_the_month_status_is_always_reported(self, monkeypatch):
        from app.models.schemas import MonthStatus
        for st in MonthStatus:
            assert self._state(monkeypatch, st)["month_status"] == st.value


class TestPublicConfig:
    """The sign-in page needs the client id before anyone is authenticated."""

    def test_it_returns_the_configured_client_id(self, monkeypatch):
        from app.config import get_settings
        from app.routers import auth as auth_router

        get_settings.cache_clear()
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "abc.apps.googleusercontent.com")
        out = auth_router.public_config()
        assert out.google_client_id == "abc.apps.googleusercontent.com"

    def test_it_exposes_no_secret(self, monkeypatch):
        """This endpoint is unauthenticated; nothing sensitive may appear."""
        from app.config import get_settings
        from app.routers import auth as auth_router

        get_settings.cache_clear()
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "super-secret-value")
        monkeypatch.setenv("JWT_SECRET", "another-secret")
        body = auth_router.public_config().model_dump_json()
        assert "super-secret-value" not in body
        assert "another-secret" not in body

    def test_it_reports_the_allowed_domains(self, monkeypatch):
        from app.config import get_settings
        from app.routers import auth as auth_router

        get_settings.cache_clear()
        monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "marrowmed.com,dailyrounds.org")
        assert auth_router.public_config().allowed_email_domains == [
            "marrowmed.com", "dailyrounds.org",
        ]


class TestCouponRuleGuards:
    """Editing a threshold must not reach back into a month already paid."""

    def test_only_rule_managers_may_edit(self):
        from app.auth.rbac import Permission, has_permission
        from app.models.schemas import Role
        for role in (Role.BDE, Role.SUB_MANAGER, Role.REGIONAL_MANAGER,
                     Role.ZONAL_MANAGER, Role.BUSINESS_HEAD):
            assert not has_permission(role, Permission.MANAGE_RULES)
        assert has_permission(Role.FINANCE_ADMIN, Permission.MANAGE_RULES)
        assert has_permission(Role.SUPER_ADMIN, Permission.MANAGE_RULES)

    def test_seed_rules_match_the_approved_workbook(self):
        from app.services.coupon_rules import seed_rules
        by_size = {r.group_size: r for r in seed_rules()}
        assert by_size["G10"].min_sales == 8
        assert by_size["G10"].min_own_sales == 5
        assert by_size["G5"].min_sales == 5
        assert by_size["G3"].min_sales == 3
        assert by_size["G3"].min_own_sales == 3

    def test_utilisation_is_reported_for_the_screen(self):
        from app.services.coupon_rules import seed_rules
        by_size = {r.group_size: r for r in seed_rules()}
        assert by_size["G10"].utilisation_pct == 0.8
        assert by_size["G5"].utilisation_pct == 1.0

    def test_period_start_is_the_first_of_the_month(self):
        from datetime import date
        from app.services.coupon_rules import period_start
        assert period_start("2026-08") == date(2026, 8, 1)

    def test_an_empty_table_falls_back_to_the_seed(self, monkeypatch):
        """A fresh install must calculate identically to the workbook."""
        from app.services import coupon_rules
        monkeypatch.setattr(coupon_rules.bq, "query", lambda *a, **k: [])
        policy = coupon_rules.policy_for_period("2026-08")
        assert policy.min_sales_for("G10", 10) == 8
        assert policy.min_own_sales_for("G3") == 3

    def test_the_policy_is_read_for_the_period_being_calculated(self, monkeypatch):
        """August reads August's rules, not today's."""
        from app.services import coupon_rules
        asked = {}
        monkeypatch.setattr(
            coupon_rules.bq, "query",
            lambda sql, params=None: asked.update(params or {}) or [],
        )
        coupon_rules.policy_for_period("2026-08")
        assert asked["start"] == "2026-08-01"


class TestTeamAdminRole:
    """A team admin maintains their own people without seeing company pay."""

    def test_it_can_manage_people_and_targets(self):
        from app.auth.rbac import Permission, has_permission
        from app.models.schemas import Role
        for p in (Permission.MANAGE_EMPLOYEES, Permission.MANAGE_HIERARCHY,
                  Permission.PROPOSE_TARGETS, Permission.APPROVE_TARGETS):
            assert has_permission(Role.TEAM_ADMIN, p)

    def test_it_cannot_see_company_wide_data(self):
        """The whole point of the role: no VIEW_BUSINESS."""
        from app.auth.rbac import Permission, has_permission
        from app.models.schemas import Role
        assert not has_permission(Role.TEAM_ADMIN, Permission.VIEW_BUSINESS)

    def test_its_scope_is_its_own_region(self):
        from app.auth.rbac import ROLE_PERMISSIONS, Principal, visible_employee_sql
        from app.models.schemas import Role
        sql, params = visible_employee_sql(Principal(
            email="lead@marrowmed.com", employee_id="NHP300", full_name="Lead",
            role=Role.TEAM_ADMIN, permissions=set(ROLE_PERMISSIONS[Role.TEAM_ADMIN]),
        ))
        assert "h.rm_id = @scope_id" in sql
        assert sql != "TRUE"

    def test_it_cannot_upload_sales_or_lock_a_month(self):
        from app.auth.rbac import Permission, has_permission
        from app.models.schemas import Role
        for p in (Permission.UPLOAD_SALES, Permission.RECALCULATE,
                  Permission.LOCK_MONTH, Permission.MANAGE_RULES):
            assert not has_permission(Role.TEAM_ADMIN, p)

    def test_a_leaver_is_closed_not_deleted(self):
        """Their past months still need an owner for every sale."""
        from datetime import date
        from app.models.schemas import Employee
        e = Employee(employee_id="NHP001", full_name="A", is_active=True)
        e.is_active, e.exit_date = False, date(2026, 10, 15)
        assert e.employee_id == "NHP001"
        assert e.exit_date == date(2026, 10, 15)


class TestStatementExport:
    """The PDF statement is scoped exactly like the dashboard it prints."""

    ROW = {
        "employee_id": "NHP001", "period": "2026-08", "designation": "BDE",
        "region": "R1", "target_units": 30, "gross_units": 37,
        "achieved_units": 35, "target_revenue": 990000,
        "qualified_revenue": 1100000.5, "disqualified_revenue": 76069.84,
        "revenue_pct": 1.111, "unit_pct": 1.1667, "arpu": 31785.68,
        "base_pct": 1.111, "bde_rate": 0.0225, "bde_incentive": 24750.01,
        "total_incentive": 24750.01, "net_payable": 24750.01, "accumulation": 0,
        "arpu_rule_applied": "ARPU 31,786 < 33,000 -> revenue achievement only",
    }
    TXNS = [
        {"payment_date_ist": "2026-08-10T12:00:00", "plan_title": "Marrow 3M",
         "college_name": "AIIMS Delhi", "coupon": "ABB10", "net_amount": 30507.6,
         "status": "QUALIFIED", "reason_detail": None},
        {"payment_date_ist": "2026-08-11T12:00:00", "plan_title": "Marrow 12M",
         "college_name": "MMC Chennai", "coupon": "ABB11", "net_amount": 30507.6,
         "status": "DISQUALIFIED", "reason_detail": "Coupon under-utilised"},
    ]

    def _wire(self, monkeypatch, *, in_scope=True, row=ROW):
        from app.routers import dashboards as dash_router
        from app.routers import exports
        monkeypatch.setattr(exports.dashboards, "own", lambda e, p: row and dict(row, employee_id=e))
        monkeypatch.setattr(exports.dashboards, "transactions", lambda e, p, limit=200: self.TXNS)
        monkeypatch.setattr(exports.employee_service, "get_by_id", lambda e: None)
        monkeypatch.setattr(exports.audit, "record", lambda *a, **k: None)
        monkeypatch.setattr(dash_router.employee_service, "is_in_scope",
                            lambda t, s, p: in_scope)
        return exports

    def test_a_bde_gets_their_own_statement(self, monkeypatch):
        exports = self._wire(monkeypatch)
        r = exports.export_statement(period="2026-08", employee_id=None,
                                     principal=principal(Role.BDE))
        assert r.media_type == "application/pdf"
        assert r.body.startswith(b"%PDF")
        assert "incentive-NHP001-2026-08.pdf" in r.headers["content-disposition"]

    def test_out_of_scope_employee_is_a_404(self, monkeypatch):
        from fastapi import HTTPException
        exports = self._wire(monkeypatch, in_scope=False)
        with pytest.raises(HTTPException) as e:
            exports.export_statement(period="2026-08", employee_id="NHP999",
                                     principal=principal(Role.BDE))
        assert e.value.status_code == 404

    def test_a_manager_exports_someone_in_scope(self, monkeypatch):
        exports = self._wire(monkeypatch)
        r = exports.export_statement(period="2026-08", employee_id="NHP002",
                                     principal=principal(Role.REGIONAL_MANAGER))
        assert "incentive-NHP002-2026-08.pdf" in r.headers["content-disposition"]

    def test_an_uncalculated_month_is_a_404_not_an_empty_pdf(self, monkeypatch):
        from fastapi import HTTPException
        exports = self._wire(monkeypatch, row=None)
        with pytest.raises(HTTPException) as e:
            exports.export_statement(period="2026-08", employee_id=None,
                                     principal=principal(Role.BDE))
        assert e.value.status_code == 404


class TestStatementFormatting:
    def test_indian_grouping(self):
        from app.services.statement_pdf import indian_grouping
        assert indian_grouping(999) == "999"
        assert indian_grouping(1000) == "1,000"
        assert indian_grouping(123456) == "1,23,456"
        assert indian_grouping(12345678.9) == "1,23,45,679"
        assert indian_grouping(-200000) == "-2,00,000"

    def test_decimal_values_from_bigquery_render(self):
        from decimal import Decimal
        from app.services.statement_pdf import build_statement
        pdf = build_statement(
            period="2026-08",
            employee={"employee_id": "NHP001", "full_name": "A BDE"},
            breakdown={"total_incentive": Decimal("24750.01"), "bde_rate": Decimal("0.0225")},
            generated_by="finance@marrowmed.com",
        )
        assert pdf.startswith(b"%PDF")


class TestClientErrorReports:
    def test_a_report_is_logged_with_the_callers_email(self, caplog):
        import logging
        from app.routers import client_errors as ce
        with caplog.at_level(logging.WARNING, logger="client_errors"):
            ce.report_client_error(
                ce.ClientError(kind="boundary", message="x is undefined", url="/team"),
                principal=principal(Role.BDE),
            )
        assert "CLIENT_ERROR" in caplog.text
        assert "nhp001@marrowmed.com" in caplog.text
        assert "x is undefined" in caplog.text

    def test_oversized_fields_are_rejected(self):
        from pydantic import ValidationError
        from app.routers.client_errors import ClientError
        with pytest.raises(ValidationError):
            ClientError(message="x" * 1001)


class TestAssignableRoles:
    """Nobody can hand out a role more powerful than their own."""

    @staticmethod
    def _values(role):
        from app.routers.admin import assignable_roles
        return {r["value"] for r in assignable_roles(principal=principal(role))}

    def test_super_admin_can_assign_every_role(self):
        assert self._values(Role.SUPER_ADMIN) == {r.value for r in Role}

    def test_finance_admin_cannot_create_a_super_admin(self):
        v = self._values(Role.FINANCE_ADMIN)
        assert "BUSINESS_HEAD" in v and "FINANCE_ADMIN" in v
        assert "SUPER_ADMIN" not in v

    def test_team_admin_cannot_hand_out_company_wide_roles(self):
        v = self._values(Role.TEAM_ADMIN)
        assert not v & {"BUSINESS_HEAD", "FINANCE_ADMIN", "SUPER_ADMIN"}


class TestDashboardSaleQueries:
    """Sale detail comes from where the calculation read it, latest run only."""

    def _capture(self, monkeypatch, src=None):
        from app.services import dashboards
        dashboards.forget_sales_sql()
        seen = {}
        monkeypatch.setattr(dashboards.source_tables, "resolve", lambda p: src)
        monkeypatch.setattr(dashboards.bq, "query",
                            lambda sql, params=None: seen.update(sql=sql, params=params) or [])
        return dashboards, seen

    def test_only_the_latest_calculation_is_read(self, monkeypatch):
        d, seen = self._capture(monkeypatch)
        d.transactions("NHP001", "2026-08")
        assert "MAX(calculation_version)" in seen["sql"]

    def test_sales_come_from_the_configured_source_table(self, monkeypatch):
        from app.services.source_tables import SourceTable
        src = SourceTable(project="p", dataset="Monthly_Sub", table="Aug_2026_v1",
                          column_map={"payment_id": "payment_id", "payment_date_ist": "payment_date_ist",
                                      "plan_title": "Plan_Title"})
        d, seen = self._capture(monkeypatch, src)
        monkeypatch.setattr(d.source_tables, "describe", lambda s: [
            {"column_name": "payment_id", "data_type": "STRING"},
            {"column_name": "payment_date_ist", "data_type": "TIMESTAMP"},
            {"column_name": "Plan_Title", "data_type": "STRING"}])
        d.transactions("NHP001", "2026-08")
        assert "`p.Monthly_Sub.Aug_2026_v1`" in seen["sql"]
        assert "raw_sales" not in seen["sql"]
        assert "`Plan_Title` AS plan_title" in seen["sql"]

    def test_uploaded_raw_sales_is_the_fallback(self, monkeypatch):
        d, seen = self._capture(monkeypatch, None)
        d.plan_mix(["NHP001"], "2026-08")
        assert "raw_sales" in seen["sql"]

    def test_the_sales_select_is_built_once_per_period(self, monkeypatch):
        d, seen = self._capture(monkeypatch, None)
        calls = []
        monkeypatch.setattr(d.source_tables, "resolve", lambda p: calls.append(p))
        d.transactions("NHP001", "2026-08")
        d.daily_trend(["NHP001"], "2026-08")
        assert calls == ["2026-08"]
        d.forget_sales_sql("2026-08")
        d.transactions("NHP001", "2026-08")
        assert calls == ["2026-08", "2026-08"]

    def test_coupon_analysis_reads_the_latest_run_for_that_person(self, monkeypatch):
        d, seen = self._capture(monkeypatch)
        d.coupon_analysis("NHP157", "2026-08")
        assert "MAX(calculated_at)" in seen["sql"]
        assert seen["params"] == {"p": "2026-08", "id": "NHP157"}

    def test_coupons_of_someone_out_of_scope_are_a_404(self, monkeypatch):
        from fastapi import HTTPException
        from app.routers import dashboards as router
        monkeypatch.setattr(router.employee_service, "is_in_scope", lambda *a: False)
        with pytest.raises(HTTPException) as e:
            router.employee_coupons("NHP999", period="2026-08", principal=principal(Role.BDE))
        assert e.value.status_code == 404


class TestConsolidatedDashboard:
    """Admins, business heads, ZMs and RMs see their whole scope added up."""

    def _capture(self, monkeypatch, rows):
        from app.services import dashboards
        seen = {}
        monkeypatch.setattr(dashboards.bq, "query",
                            lambda sql, params=None: seen.update(sql=sql, params=params) or rows)
        return dashboards, seen

    def test_sums_are_scoped_and_ratios_recomputed(self, monkeypatch):
        d, seen = self._capture(monkeypatch, [{
            "people": 3, "headcount": 2, "target_units": 40, "achieved_units": 30,
            "gross_units": 32, "gross_net_revenue": 960000,
            "target_revenue": 1000000, "qualified_revenue": 800000,
        }])
        out = d.consolidated(principal(Role.REGIONAL_MANAGER), "2026-08")
        assert "h.rm_id = @scope_id" in seen["sql"]
        assert seen["params"]["p"] == "2026-08"
        assert out["people"] == 3
        assert out["unit_pct"] == 0.75
        assert out["revenue_pct"] == 0.8
        assert out["arpu"] == 30000

    def test_nobody_in_scope_is_none(self, monkeypatch):
        d, _ = self._capture(monkeypatch, [{"people": 0}])
        assert d.consolidated(principal(Role.ZONAL_MANAGER), "2026-08") is None

    def test_a_bde_cannot_ask_for_it(self):
        import asyncio
        from fastapi import HTTPException
        from app.auth.deps import require
        from app.auth.rbac import Permission
        guard = require(Permission.VIEW_TEAM)
        with pytest.raises(HTTPException) as e:
            asyncio.run(guard(principal=principal(Role.BDE)))
        assert e.value.status_code == 403

    def test_the_scope_is_named_for_the_caller(self):
        from app.routers.dashboards import _scope_label
        assert _scope_label(principal(Role.SUPER_ADMIN)) == "Company"
        rm = principal(Role.REGIONAL_MANAGER)
        rm.region = "South"
        assert _scope_label(rm) == "South region"
