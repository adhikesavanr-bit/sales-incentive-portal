"""Incentive engine. Covers every slab and the ARPU rule in both directions."""
from __future__ import annotations

import pytest

from app.engines.incentive import (
    DEFAULT_SLAB_TABLES,
    EmployeeMonthAggregate,
    IncentiveConfig,
    ManualAdjustment,
    QuarterlyRegionInput,
    apply_submanager_incentive,
    calculate_bde_incentive,
    calculate_period,
    calculate_rm_quarterly,
    finalise,
)
from app.models.schemas import QualificationStatus, SlabScope, Target, TransactionQualification

CFG = IncentiveConfig()
ARPU = 33000.0


def agg(emp="NHP001", *, units, disq_units=0, net, disq_net=0.0, fc=0):
    return EmployeeMonthAggregate(
        employee_id=emp, period="2026-08",
        gross_units=units, disqualified_units=disq_units, foundation_units=fc,
        gross_revenue=net * 1.18, gross_net_revenue=net, disqualified_revenue=disq_net,
    )


class TestSlabLookup:
    """Excel LOOKUP: the last threshold <= value wins."""

    @pytest.mark.parametrize("pct,rate", [
        (0.00, 0.0), (0.4999, 0.0),
        (0.50, 0.005), (0.7999, 0.005),
        (0.80, 0.01), (0.9999, 0.01),
        (1.00, 0.0225), (1.2999, 0.0225),
        (1.30, 0.03), (5.00, 0.03),
    ])
    def test_bde_monthly_slabs(self, pct, rate):
        assert DEFAULT_SLAB_TABLES[SlabScope.BDE_MONTHLY].lookup(pct).rate == rate

    @pytest.mark.parametrize("pct,rate", [
        (0.0, 0.0), (0.7999, 0.0), (0.80, 0.001), (1.00, 0.0015), (1.30, 0.0025),
    ])
    def test_submanager_slabs(self, pct, rate):
        assert DEFAULT_SLAB_TABLES[SlabScope.SUBMANAGER_MONTHLY].lookup(pct).rate == rate

    @pytest.mark.parametrize("pct,rate", [
        (0.0, 0.0), (0.80, 0.0025), (1.00, 0.0035), (1.30, 0.005),
    ])
    def test_team_owner_slabs(self, pct, rate):
        assert DEFAULT_SLAB_TABLES[SlabScope.TEAM_OWNER_QUARTERLY].lookup(pct).rate == rate

    @pytest.mark.parametrize("pct,rate", [
        (0.0, 0.0), (0.80, 0.002), (1.00, 0.0025), (1.30, 0.0035),
    ])
    def test_zm_slabs(self, pct, rate):
        assert DEFAULT_SLAB_TABLES[SlabScope.ZM_NINE_MONTH].lookup(pct).rate == rate

    def test_ascending_order_is_enforced(self):
        from app.engines.incentive import Slab, SlabTable
        with pytest.raises(ValueError, match="ascending"):
            SlabTable(scope=SlabScope.BDE_MONTHLY,
                      slabs=[Slab(1.0, 0.02), Slab(0.5, 0.005)])


class TestArpuRule:
    """Checklists row 7 — the rule that decides column Q."""

    def test_high_arpu_takes_the_higher_of_units_and_revenue(self):
        # 100 units of Rs.40,000 net: ARPU 40,000 >= 33,000.
        # Units 100/100 = 100%; revenue 4,000,000 / 3,300,000 = 121%.
        b = calculate_bde_incentive(
            agg(units=100, net=4_000_000.0),
            Target(employee_id="NHP001", period="2026-08",
                   target_units=100, winner_units=130),
            config=CFG,
        )
        assert b.arpu == pytest.approx(40_000)
        assert b.unit_pct == pytest.approx(1.00)
        assert b.revenue_pct == pytest.approx(4_000_000 / 3_300_000)
        assert b.base_pct == pytest.approx(b.revenue_pct)   # the higher one
        assert "higher of" in b.arpu_rule_applied

    def test_high_arpu_uses_units_when_disqualification_drags_revenue_down(self):
        """Units beat revenue once disqualified sales are stripped out.

        Without disqualification a high-ARPU seller always scores higher on
        revenue than on units, so this is the case the MAX() actually protects:
        110 achieved units on a 100 target is 110%, while the surviving
        Rs.36 lakh against a Rs.33 lakh target is only 109.1%.
        """
        b = calculate_bde_incentive(
            agg(units=120, disq_units=10, net=4_800_000.0, disq_net=1_200_000.0),
            Target(employee_id="NHP001", period="2026-08",
                   target_units=100, winner_units=130),
            config=CFG,
        )
        assert b.arpu == pytest.approx(40_000)
        assert b.unit_pct == pytest.approx(1.10)
        assert b.revenue_pct == pytest.approx(3_600_000 / 3_300_000)
        assert b.base_pct == pytest.approx(1.10)
        assert b.base_pct > b.revenue_pct

    def test_low_arpu_ignores_units_entirely(self):
        # 120 units of Rs.25,000: ARPU 25,000 < 33,000.
        # Units would give 120%, but only revenue (90.9%) may count.
        b = calculate_bde_incentive(
            agg(units=120, net=120 * 25_000.0),
            Target(employee_id="NHP001", period="2026-08",
                   target_units=100, winner_units=130),
            config=CFG,
        )
        assert b.arpu == pytest.approx(25_000)
        assert b.unit_pct == pytest.approx(1.20)
        assert b.base_pct == pytest.approx(b.revenue_pct)
        assert b.base_pct < b.unit_pct
        assert "revenue achievement only" in b.arpu_rule_applied
        # and the slab drops from 2.25% to 1% as a result
        assert b.bde_rate == 0.01

    def test_arpu_is_computed_on_gross_units_not_qualified(self):
        """ARPU!G = gross net revenue / gross units, disqualification aside."""
        b = calculate_bde_incentive(
            agg(units=100, disq_units=40, net=3_500_000.0, disq_net=1_400_000.0),
            Target(employee_id="NHP001", period="2026-08", target_units=100,
                   winner_units=130),
            config=CFG,
        )
        assert b.arpu == pytest.approx(35_000)   # 3,500,000 / 100, not / 60


class TestBdeCalculation:
    def test_columns_reproduce_the_workbook_arithmetic(self):
        b = calculate_bde_incentive(
            agg(units=50, disq_units=5, net=1_800_000.0, disq_net=180_000.0, fc=3),
            Target(employee_id="NHP001", period="2026-08",
                   target_units=40, winner_units=52),
            config=CFG,
        )
        assert b.achieved_units == 45                       # col J = G - H
        assert b.unit_pct == pytest.approx(45 / 40)         # col K
        assert b.target_revenue == pytest.approx(40 * ARPU)  # col L
        assert b.qualified_revenue == pytest.approx(1_620_000)  # col O
        assert b.revenue_pct == pytest.approx(1_620_000 / 1_320_000)
        assert b.foundation_units == 3                      # col I
        # col S = (rate + incremental) * O
        assert b.bde_incentive == pytest.approx(b.bde_rate * 1_620_000)

    def test_zero_target_scores_zero_rather_than_raising(self):
        """IFERROR(J/E, 0) — a missing target must not blow up the run."""
        b = calculate_bde_incentive(agg(units=10, net=350_000.0), None, config=CFG)
        assert b.unit_pct == 0.0 and b.revenue_pct == 0.0
        assert b.bde_incentive == 0.0

    def test_zero_sales_is_marked_inactive(self):
        """Treated as an exit for headcount and averages (DATA_MAPPING 7.4)."""
        b = calculate_bde_incentive(
            agg(units=0, net=0.0),
            Target(employee_id="NHP001", period="2026-08",
                   target_units=50, winner_units=65),
            config=CFG,
        )
        assert b.is_active is False
        assert b.total_incentive == 0.0 or finalise(b, CFG).total_incentive == 0.0

    def test_incremental_slab_adds_to_the_rate(self):
        b = calculate_bde_incentive(
            agg(units=100, net=4_000_000.0),
            Target(employee_id="NHP001", period="2026-08",
                   target_units=100, winner_units=130),
            adjustment=ManualAdjustment(
                employee_id="NHP001", period="2026-08",
                incremental_pct=0.0025, reason="First-year MBBS 125 units",
                entered_by="finance@marrowmed.com",
            ),
            config=CFG,
        )
        assert b.bde_incentive == pytest.approx((b.bde_rate + 0.0025) * b.qualified_revenue)


class TestSubmanager:
    def test_rolls_up_reports_and_applies_the_submanager_slab(self, employees):
        from app.models.schemas import IncentiveBreakdown
        bds = {
            "NHP001": IncentiveBreakdown(employee_id="NHP001", period="2026-08",
                                         target_revenue=1_000_000,
                                         qualified_revenue=900_000),
            "NHP002": IncentiveBreakdown(employee_id="NHP002", period="2026-08"),
        }
        apply_submanager_incentive(bds, employees, CFG)
        m = bds["NHP002"]
        assert m.submanager_target_revenue == 1_000_000     # col T
        assert m.submanager_achieved_revenue == 900_000     # col U
        assert m.submanager_pct == pytest.approx(0.90)      # col V
        assert m.submanager_rate == 0.001                   # 80-99.99% band
        assert m.submanager_incentive == pytest.approx(900)  # col W

    def test_below_eighty_percent_pays_nothing(self, employees):
        from app.models.schemas import IncentiveBreakdown
        bds = {
            "NHP001": IncentiveBreakdown(employee_id="NHP001", period="2026-08",
                                         target_revenue=1_000_000,
                                         qualified_revenue=700_000),
            "NHP002": IncentiveBreakdown(employee_id="NHP002", period="2026-08"),
        }
        apply_submanager_incentive(bds, employees, CFG)
        assert bds["NHP002"].submanager_incentive == 0.0


class TestCapAndTotals:
    def test_total_is_S_plus_W_plus_AB_plus_AC(self):
        from app.models.schemas import IncentiveBreakdown
        b = IncentiveBreakdown(
            employee_id="NHP001", period="2026-08",
            bde_incentive=100_000, submanager_incentive=20_000,
            other_incentive=5_000, prior_period_adjustment=-2_000,
        )
        finalise(b, CFG)
        assert b.total_incentive == pytest.approx(123_000)
        assert b.accumulation == 0.0
        assert b.net_payable == pytest.approx(123_000)

    def test_amounts_above_two_lakh_accumulate(self):
        from app.models.schemas import IncentiveBreakdown
        b = IncentiveBreakdown(employee_id="NHP001", period="2026-08",
                               bde_incentive=544_702.83)
        finalise(b, CFG)
        assert b.net_payable == pytest.approx(200_000)
        assert b.accumulation == pytest.approx(344_702.83)

    def test_exactly_at_the_cap_accumulates_nothing(self):
        from app.models.schemas import IncentiveBreakdown
        b = IncentiveBreakdown(employee_id="NHP001", period="2026-08",
                               bde_incentive=200_000)
        finalise(b, CFG)
        assert b.accumulation == 0.0 and b.net_payable == 200_000


class TestRmQuarterly:
    def test_quarter_payout_splits_across_months_by_contribution(self):
        out = calculate_rm_quarterly(
            QuarterlyRegionInput(
                region="R2-Akif", rm_employee_id="NHP212",
                quarter_target_units=2000,
                monthly_qualified_revenue={
                    "2026-07": 5_830_468.64,
                    "2026-08": 78_196_048.31,
                    "2026-09": 0.0,
                },
            ),
            CFG,
        )
        assert out.quarter_target_revenue == pytest.approx(2000 * ARPU)
        assert out.achieved_pct > 1.0
        assert out.payout_rate == 0.0035          # 100-129.99% band
        assert out.quarter_payout == pytest.approx(out.quarter_achieved_revenue * 0.0035)
        assert sum(out.monthly_split.values()) == pytest.approx(out.quarter_payout)
        assert out.monthly_split["2026-09"] == 0.0

    def test_zero_achievement_does_not_divide_by_zero(self):
        out = calculate_rm_quarterly(
            QuarterlyRegionInput(region="R9", rm_employee_id="NHP138",
                                 quarter_target_units=500,
                                 monthly_qualified_revenue={"2026-08": 0.0}),
            CFG,
        )
        assert out.quarter_payout == 0.0 and out.monthly_split["2026-08"] == 0.0


class TestPeriodOrchestration:
    def test_employee_with_a_target_but_no_sales_still_gets_a_row(self, employees, targets):
        out = calculate_period([], "2026-08", employees, targets, config=CFG)
        assert "NHP001" in out
        assert out["NHP001"].is_active is False
        assert out["NHP001"].total_incentive == 0.0

    def test_unattributed_transactions_reach_nobody(self, employees, targets):
        txns = [TransactionQualification(
            payment_id="p1", employee_id=None, coupon_signature=None,
            status=QualificationStatus.UNATTRIBUTED, net_amount=35_000, paid_amount=41_300,
        )]
        out = calculate_period(txns, "2026-08", employees, targets, config=CFG)
        assert all(b.gross_units == 0 for b in out.values())


class TestNonFieldCoupons:
    """Teams outside the field scheme are audited, not paid, by this engine."""

    @staticmethod
    def _txn(emp, *, is_field, net=100_000.0):
        return TransactionQualification(
            payment_id=f"p-{emp}-{is_field}", employee_id=emp,
            coupon_signature="sig", status=QualificationStatus.QUALIFIED,
            coupon_region="R1-Ajeet" if is_field else "Inside Sales",
            is_field=is_field, net_amount=net, paid_amount=net * 1.18,
        )

    def test_non_field_sales_do_not_aggregate(self):
        from app.engines.incentive import aggregate_transactions
        out = aggregate_transactions(
            [self._txn("NHP001", is_field=True), self._txn("NHP308", is_field=False)],
            "2026-08",
        )
        assert set(out) == {"NHP001"}

    def test_a_non_field_seller_gets_no_row_at_all(self, employees, targets):
        """NHP308 sold 158 units on Inside Sales coupons in August.

        Without this, they appear at zero incentive against a zero target,
        which reads as a calculation failure rather than a different scheme.
        """
        out = calculate_period(
            [self._txn("NHP308", is_field=False)], "2026-08",
            employees, targets, config=CFG,
        )
        assert "NHP308" not in out

    def test_field_sales_are_unaffected(self, employees, targets):
        out = calculate_period(
            [self._txn("NHP001", is_field=True, net=3_300_000.0)], "2026-08",
            employees, targets, config=CFG,
        )
        assert out["NHP001"].qualified_revenue == pytest.approx(3_300_000.0)

    def test_the_flag_defaults_to_field(self):
        """An older row without the flag must not silently drop out."""
        t = TransactionQualification(
            payment_id="p1", employee_id="NHP001", coupon_signature="s",
            status=QualificationStatus.QUALIFIED, net_amount=1.0, paid_amount=1.18,
        )
        assert t.is_field is True
