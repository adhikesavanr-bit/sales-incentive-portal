"""Hierarchy and employee mapping."""
from __future__ import annotations

import pytest

from app.auth.rbac import ROLE_PERMISSIONS, Permission, Principal, visible_employee_sql
from app.engines.incentive import (
    IncentiveConfig,
    apply_submanager_incentive,
    calculate_period,
)
from app.models.schemas import Employee, IncentiveBreakdown, Role


class TestEmployeeMapping:
    """initial -> employee id -> name -> email, from the supplied masters."""

    def test_coupon_initial_resolves_through_the_coupon_master(self):
        from tests.conftest import sig
        s = sig("SMP31MME", "NHP166", "58ad3474a2393a143df57f12")
        assert s.coupon_code == "SMP31MME"
        assert s.employee_id == "NHP166"

    def test_clubbing_key_is_employee_college_and_activation(self):
        from tests.conftest import sig
        a = sig("SMP29MMF", "NHP166", "COLX")
        b = sig("SMP29FCF", "NHP166", "COLX", group="13_5_FIELDG10")
        assert a.clubbing_key == b.clubbing_key
        assert a.is_foundation is False and b.is_foundation is True

    def test_foundation_flag_follows_the_discount_group(self):
        from tests.conftest import sig
        assert sig("X01MMA", "E1", "C1", group="1_5_FIELDG10").is_foundation is False
        assert sig("X01FCA", "E1", "C1", group="13_5_FMGEG5",
                   size="G5").is_foundation is True


class TestHierarchyVisibility:
    @pytest.fixture
    def org(self) -> dict[str, Employee]:
        """Mirrors R6-Independent(Biswadip): a region with no RM."""
        return {
            "B1": Employee(employee_id="B1", full_name="BDE one", role=Role.BDE,
                           region="R6", zone="Z2", rm_id=None, zm_id="ZM1"),
            "B2": Employee(employee_id="B2", full_name="BDE two", role=Role.BDE,
                           region="R6", zone="Z2", rm_id=None, zm_id="ZM1"),
            "ZM1": Employee(employee_id="ZM1", full_name="Zonal", role=Role.ZONAL_MANAGER,
                            zone="Z2"),
        }

    def test_independent_region_with_no_rm_is_allowed(self, org):
        assert org["B1"].rm_id is None
        assert org["B1"].zm_id == "ZM1"

    def test_each_level_keys_on_a_different_column(self):
        levels = {
            Role.BDE: "h.employee_id",
            Role.SUB_MANAGER: "h.submanager_id",
            Role.REGIONAL_MANAGER: "h.rm_id",
            Role.ZONAL_MANAGER: "h.zm_id",
        }
        for role, column in levels.items():
            sql, _ = visible_employee_sql(Principal(
                email="x@marrowmed.com", employee_id="X", full_name="X",
                role=role, permissions=set(ROLE_PERMISSIONS[role]),
            ))
            assert column in sql


class TestSubmanagerRollup:
    def test_only_direct_reports_roll_into_a_submanager(self):
        employees = {
            "R1": Employee(employee_id="R1", full_name="r1", submanager_id="M1"),
            "R2": Employee(employee_id="R2", full_name="r2", submanager_id="M1"),
            "R3": Employee(employee_id="R3", full_name="r3", submanager_id="M2"),
            "M1": Employee(employee_id="M1", full_name="m1", role=Role.SUB_MANAGER),
            "M2": Employee(employee_id="M2", full_name="m2", role=Role.SUB_MANAGER),
        }
        bds = {
            k: IncentiveBreakdown(employee_id=k, period="2026-08",
                                  target_revenue=1_000_000, qualified_revenue=1_000_000)
            for k in employees
        }
        for m in ("M1", "M2"):
            bds[m].target_revenue = 0
            bds[m].qualified_revenue = 0
        apply_submanager_incentive(bds, employees, IncentiveConfig())
        assert bds["M1"].submanager_target_revenue == 2_000_000
        assert bds["M2"].submanager_target_revenue == 1_000_000

    def test_an_employee_with_neither_target_nor_sales_gets_no_row(self, employees, targets):
        """The RM in the fixture has no BDE target and made no sale.

        The month produces rows for people with a target or a sale; an RM is
        paid on the quarterly cycle instead, so a zero monthly row would be
        misleading rather than merely empty.
        """
        out = calculate_period([], "2026-08", employees, targets,
                               config=IncentiveConfig())
        assert set(out) == {"NHP001", "NHP002"}
        assert out["NHP002"].submanager_incentive == 0.0
