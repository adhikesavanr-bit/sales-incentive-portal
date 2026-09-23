"""Incentive engine.

Reproduces `Incentive Report- Output` column by column. Every function returns
a full breakdown so Finance can audit an amount back to the workbook cell it
came from.

Pure functions, no IO. See DATA_MAPPING.md §6 and §7.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.schemas import (
    Employee,
    IncentiveBreakdown,
    QualificationStatus,
    SlabHit,
    SlabScope,
    Target,
    TransactionQualification,
)


# ---------------------------------------------------------------------------
# Slab tables
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Slab:
    """One row of a Policy lookup table. `threshold` is an inclusive lower bound."""

    threshold: float
    rate: float


@dataclass
class SlabTable:
    """Excel LOOKUP semantics: the last row whose threshold <= value wins.

    Thresholds must be ascending, exactly as in the Policy sheet.
    """

    scope: SlabScope
    slabs: list[Slab]
    effective_from: str | None = None
    source_note: str | None = None

    def __post_init__(self) -> None:
        if not self.slabs:
            raise ValueError(f"{self.scope} has no slabs")
        thresholds = [s.threshold for s in self.slabs]
        if thresholds != sorted(thresholds):
            raise ValueError(f"{self.scope} slabs are not in ascending order")

    def lookup(self, value: float) -> SlabHit:
        hit = self.slabs[0]
        for slab in self.slabs:
            if value >= slab.threshold:
                hit = slab
            else:
                break
        return SlabHit(
            scope=self.scope,
            input_value=value,
            threshold=hit.threshold,
            rate=hit.rate,
        )


# Defaults seeded from Policy!L12:M31 of Field_Incentive_Report_Aug_2026.xlsx
# and confirmed against the current policy table supplied by Finance in
# Sep 2026. All four scopes enter at 80% except BDE, which enters at 50%.
# The application loads these from the `incentive_rules` table at runtime; these
# constants exist so the engine is testable standalone and so a fresh deployment
# has something correct to seed from.
DEFAULT_SLAB_TABLES: dict[SlabScope, SlabTable] = {
    SlabScope.BDE_MONTHLY: SlabTable(
        scope=SlabScope.BDE_MONTHLY,
        slabs=[Slab(0.0, 0.0), Slab(0.50, 0.005), Slab(0.80, 0.01),
               Slab(1.00, 0.0225), Slab(1.30, 0.03)],
        source_note="Policy!L12:M16",
    ),
    SlabScope.SUBMANAGER_MONTHLY: SlabTable(
        scope=SlabScope.SUBMANAGER_MONTHLY,
        slabs=[Slab(0.0, 0.0), Slab(0.80, 0.001), Slab(1.00, 0.0015),
               Slab(1.30, 0.0025)],
        source_note=(
            "Policy!L18:M21. Confirmed by Finance, Sep 2026: the entry "
            "threshold is 80%. The 75% figure elsewhere in the sheet is an "
            "earlier policy that is no longer wired to anything."
        ),
    ),
    SlabScope.TEAM_OWNER_QUARTERLY: SlabTable(
        scope=SlabScope.TEAM_OWNER_QUARTERLY,
        slabs=[Slab(0.0, 0.0), Slab(0.80, 0.0025), Slab(1.00, 0.0035),
               Slab(1.30, 0.005)],
        source_note=(
            "Policy!L23:M26. Confirmed by Finance, Sep 2026: entry at 80%. "
            "Paid quarterly, pro-rated across the months of the quarter."
        ),
    ),
    SlabScope.ZM_NINE_MONTH: SlabTable(
        scope=SlabScope.ZM_NINE_MONTH,
        slabs=[Slab(0.0, 0.0), Slab(0.80, 0.002), Slab(1.00, 0.0025),
               Slab(1.30, 0.0035)],
        source_note=(
            "Policy!L28:M31. Confirmed by Finance, Sep 2026: entry at 80%."
        ),
    ),
    SlabScope.FIRST_YEAR_MBBS_UNITS: SlabTable(
        scope=SlabScope.FIRST_YEAR_MBBS_UNITS,
        slabs=[Slab(0, 0.0), Slab(75, 0.0005), Slab(100, 0.001), Slab(125, 0.0025)],
        source_note="Policy!L5:M8. Column R was 0 for every employee Apr-Aug 2026.",
    ),
}


@dataclass
class IncentiveConfig:
    arpu: float = 33000.0                 # Policy!B3
    arpu_threshold: float = 33000.0       # Checklists row 7
    monthly_cap: float = 200000.0         # Incentive Report col AD
    treat_zero_sales_as_exit: bool = True
    slab_tables: dict[SlabScope, SlabTable] = field(
        default_factory=lambda: dict(DEFAULT_SLAB_TABLES)
    )

    def table(self, scope: SlabScope) -> SlabTable:
        return self.slab_tables[scope]


@dataclass
class ManualAdjustment:
    """Columns R, AB, AC — always entered by a named person, never derived."""

    employee_id: str
    period: str
    incremental_pct: float = 0.0          # col R
    other_incentive: float = 0.0          # col AB
    prior_period_adjustment: float = 0.0  # col AC
    reason: str = ""
    entered_by: str = ""


# ---------------------------------------------------------------------------
# Stage 1 — aggregate transactions into per-employee monthly figures
# ---------------------------------------------------------------------------
@dataclass
class EmployeeMonthAggregate:
    employee_id: str
    period: str
    gross_units: int = 0
    disqualified_units: int = 0
    foundation_units: int = 0
    gross_revenue: float = 0.0        # incl. GST, col M
    gross_net_revenue: float = 0.0    # excl. GST, before disqualification
    disqualified_revenue: float = 0.0  # col N


def aggregate_transactions(
    transactions: list[TransactionQualification],
    period: str,
) -> dict[str, EmployeeMonthAggregate]:
    """Fold qualified/disqualified transactions into the workbook's pivot shape.

    Mirrors the three pivots on `Target Achieved- Input`: Gross Sales,
    Disqualified Sales and Foundation Sales.
    """
    out: dict[str, EmployeeMonthAggregate] = {}
    for t in transactions:
        if t.employee_id is None or t.status is QualificationStatus.UNATTRIBUTED:
            continue
        # Sales on a non-field coupon belong to another scheme. Counting them
        # here would give those sellers a zero-target, zero-incentive row that
        # reads as a mistake to everyone who sees it.
        if not t.is_field:
            continue
        agg = out.setdefault(
            t.employee_id, EmployeeMonthAggregate(t.employee_id, period)
        )
        agg.gross_units += 1
        agg.gross_revenue += t.paid_amount
        agg.gross_net_revenue += t.net_amount
        if t.status is QualificationStatus.DISQUALIFIED:
            agg.disqualified_units += 1
            agg.disqualified_revenue += t.net_amount
        if t.is_foundation:
            agg.foundation_units += 1
    return out


# ---------------------------------------------------------------------------
# Stage 2 — the individual (BDE) calculation
# ---------------------------------------------------------------------------
def calculate_bde_incentive(
    agg: EmployeeMonthAggregate,
    target: Target | None,
    employee: Employee | None = None,
    adjustment: ManualAdjustment | None = None,
    config: IncentiveConfig | None = None,
) -> IncentiveBreakdown:
    """Columns E through S, plus the ARPU rule at column Q."""
    cfg = config or IncentiveConfig()
    b = IncentiveBreakdown(employee_id=agg.employee_id, period=agg.period)

    if employee:
        b.designation = employee.designation
        b.region = employee.region
        b.zone = employee.zone

    b.target_units = target.target_units if target else 0.0
    b.winner_units = target.winner_units if target else 0.0

    b.gross_units = agg.gross_units
    b.disqualified_units = agg.disqualified_units
    b.foundation_units = agg.foundation_units
    b.achieved_units = agg.gross_units - agg.disqualified_units          # col J

    # col K — IFERROR(J/E, 0): a zero target must not raise, it scores zero.
    b.unit_pct = (b.achieved_units / b.target_units) if b.target_units else 0.0

    b.target_revenue = b.target_units * cfg.arpu                          # col L
    b.gross_revenue = agg.gross_revenue                                   # col M
    b.gross_net_revenue = agg.gross_net_revenue
    b.disqualified_revenue = agg.disqualified_revenue                     # col N
    b.qualified_revenue = agg.gross_net_revenue - agg.disqualified_revenue  # col O
    b.revenue_pct = (b.qualified_revenue / b.target_revenue) if b.target_revenue else 0.0

    # --- the ARPU rule (col Q) -----------------------------------------
    # ARPU is computed on GROSS revenue over GROSS units, matching ARPU!G = E/F.
    b.arpu = (agg.gross_net_revenue / agg.gross_units) if agg.gross_units else 0.0
    b.arpu_threshold = cfg.arpu_threshold
    if b.arpu >= cfg.arpu_threshold:
        b.base_pct = max(b.revenue_pct, b.unit_pct)
        b.arpu_rule_applied = (
            f"ARPU {b.arpu:,.0f} >= {cfg.arpu_threshold:,.0f} -> "
            f"higher of units ({b.unit_pct:.2%}) and revenue ({b.revenue_pct:.2%})"
        )
    else:
        b.base_pct = b.revenue_pct
        b.arpu_rule_applied = (
            f"ARPU {b.arpu:,.0f} < {cfg.arpu_threshold:,.0f} -> "
            f"revenue achievement only ({b.revenue_pct:.2%})"
        )

    b.incremental_pct = adjustment.incremental_pct if adjustment else 0.0  # col R

    hit = cfg.table(SlabScope.BDE_MONTHLY).lookup(b.base_pct)
    b.slab_hits.append(hit)
    b.bde_rate = hit.rate
    # col S = (LOOKUP(Q, Policy!L12:M16) + R) * O
    b.bde_incentive = (hit.rate + b.incremental_pct) * b.qualified_revenue

    if adjustment:
        b.other_incentive = adjustment.other_incentive
        b.prior_period_adjustment = adjustment.prior_period_adjustment
        if adjustment.reason:
            b.notes.append(f"Manual adjustment: {adjustment.reason}")

    b.is_active = agg.gross_units > 0 or not cfg.treat_zero_sales_as_exit
    return b


# ---------------------------------------------------------------------------
# Stage 3 — sub-manager (TM / SBM / Sr.BDE) roll-up
# ---------------------------------------------------------------------------
def apply_submanager_incentive(
    breakdowns: dict[str, IncentiveBreakdown],
    employees: dict[str, Employee],
    config: IncentiveConfig | None = None,
) -> None:
    """Columns T-W, computed in place.

    T = sum of reports' target revenue, U = sum of reports' qualified revenue,
    V = U/T, W = LOOKUP(V, Policy!L18:M21) * U.
    """
    cfg = config or IncentiveConfig()
    reports: dict[str, list[str]] = defaultdict(list)
    for emp in employees.values():
        if emp.submanager_id:
            reports[emp.submanager_id].append(emp.employee_id)

    for manager_id, report_ids in reports.items():
        b = breakdowns.get(manager_id)
        if b is None:
            continue
        b.submanager_target_revenue = sum(
            breakdowns[r].target_revenue for r in report_ids if r in breakdowns
        )
        b.submanager_achieved_revenue = sum(
            breakdowns[r].qualified_revenue for r in report_ids if r in breakdowns
        )
        b.submanager_pct = (
            b.submanager_achieved_revenue / b.submanager_target_revenue
            if b.submanager_target_revenue else 0.0
        )
        hit = cfg.table(SlabScope.SUBMANAGER_MONTHLY).lookup(b.submanager_pct)
        b.slab_hits.append(hit)
        b.submanager_rate = hit.rate
        b.submanager_incentive = hit.rate * b.submanager_achieved_revenue


# ---------------------------------------------------------------------------
# Stage 4 — RM quarterly pro-rata
# ---------------------------------------------------------------------------
@dataclass
class QuarterlyRegionInput:
    region: str
    rm_employee_id: str
    quarter_target_units: float
    monthly_qualified_revenue: dict[str, float]   # {"2026-07": 5_830_468.64, ...}


@dataclass
class QuarterlyPayout:
    region: str
    rm_employee_id: str
    quarter_target_revenue: float
    quarter_achieved_revenue: float
    achieved_pct: float
    payout_rate: float
    quarter_payout: float
    monthly_split: dict[str, float]
    slab_hit: SlabHit


def calculate_rm_quarterly(
    inp: QuarterlyRegionInput,
    config: IncentiveConfig | None = None,
) -> QuarterlyPayout:
    """`RM Calculation Pro Rata`.

    The quarter's payout is earned on the quarter's achievement, then split
    across the months in proportion to each month's revenue contribution.
    """
    cfg = config or IncentiveConfig()
    target_revenue = inp.quarter_target_units * cfg.arpu
    achieved = sum(inp.monthly_qualified_revenue.values())
    pct = (achieved / target_revenue) if target_revenue else 0.0

    hit = cfg.table(SlabScope.TEAM_OWNER_QUARTERLY).lookup(pct)
    quarter_payout = achieved * hit.rate

    split = {
        month: (quarter_payout * (rev / achieved) if achieved else 0.0)
        for month, rev in inp.monthly_qualified_revenue.items()
    }
    return QuarterlyPayout(
        region=inp.region,
        rm_employee_id=inp.rm_employee_id,
        quarter_target_revenue=target_revenue,
        quarter_achieved_revenue=achieved,
        achieved_pct=pct,
        payout_rate=hit.rate,
        quarter_payout=quarter_payout,
        monthly_split=split,
        slab_hit=hit,
    )


def calculate_zm_period(
    zone: str,
    zm_employee_id: str,
    period_target_units: float,
    monthly_qualified_revenue: dict[str, float],
    config: IncentiveConfig | None = None,
) -> QuarterlyPayout:
    """`ZM Incentive Calculation` — same shape, 9-month base, ZM slab."""
    cfg = config or IncentiveConfig()
    target_revenue = period_target_units * cfg.arpu
    achieved = sum(monthly_qualified_revenue.values())
    pct = (achieved / target_revenue) if target_revenue else 0.0
    hit = cfg.table(SlabScope.ZM_NINE_MONTH).lookup(pct)
    payout = achieved * hit.rate
    split = {
        m: (payout * (r / achieved) if achieved else 0.0)
        for m, r in monthly_qualified_revenue.items()
    }
    return QuarterlyPayout(
        region=zone,
        rm_employee_id=zm_employee_id,
        quarter_target_revenue=target_revenue,
        quarter_achieved_revenue=achieved,
        achieved_pct=pct,
        payout_rate=hit.rate,
        quarter_payout=payout,
        monthly_split=split,
        slab_hit=hit,
    )


# ---------------------------------------------------------------------------
# Stage 5 — totals and the cap
# ---------------------------------------------------------------------------
def finalise(
    b: IncentiveBreakdown,
    config: IncentiveConfig | None = None,
    calculation_version: int = 1,
) -> IncentiveBreakdown:
    """Columns AD, AE, AF.

    AF = S + W + AB + AC. Note the RM quarterly payout sits in column AA and is
    NOT part of AF in the workbook; it is paid on the quarterly cycle. We keep
    that behaviour and expose `rm_incentive` separately.
    """
    cfg = config or IncentiveConfig()
    b.total_incentive = (
        b.bde_incentive
        + b.submanager_incentive
        + b.other_incentive
        + b.prior_period_adjustment
    )
    b.accumulation = max(0.0, b.total_incentive - cfg.monthly_cap)   # col AD
    b.net_payable = b.total_incentive - b.accumulation               # col AE
    b.calculation_version = calculation_version
    b.calculated_at = datetime.now(timezone.utc)
    return b


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def calculate_period(
    transactions: list[TransactionQualification],
    period: str,
    employees: dict[str, Employee],
    targets: dict[str, Target],
    adjustments: dict[str, ManualAdjustment] | None = None,
    config: IncentiveConfig | None = None,
    calculation_version: int = 1,
) -> dict[str, IncentiveBreakdown]:
    """Run the whole month for every employee who has a target or a sale."""
    cfg = config or IncentiveConfig()
    adj = adjustments or {}
    aggregates = aggregate_transactions(transactions, period)

    # Employees with a target but no sales still get a row, at zero.
    for emp_id in targets:
        aggregates.setdefault(emp_id, EmployeeMonthAggregate(emp_id, period))

    breakdowns = {
        emp_id: calculate_bde_incentive(
            agg,
            targets.get(emp_id),
            employees.get(emp_id),
            adj.get(emp_id),
            cfg,
        )
        for emp_id, agg in aggregates.items()
    }

    apply_submanager_incentive(breakdowns, employees, cfg)

    for b in breakdowns.values():
        finalise(b, cfg, calculation_version)
    return breakdowns
