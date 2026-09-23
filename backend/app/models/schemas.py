"""Domain models. These are the contract between the engines, the API and the UI."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class Role(str, Enum):
    """Derived from `Incentive Report- Output` column C plus the admin tiers."""

    SUPER_ADMIN = "SUPER_ADMIN"
    FINANCE_ADMIN = "FINANCE_ADMIN"
    BUSINESS_HEAD = "BUSINESS_HEAD"
    ZONAL_MANAGER = "ZONAL_MANAGER"
    REGIONAL_MANAGER = "REGIONAL_MANAGER"
    SUB_MANAGER = "SUB_MANAGER"  # workbook designation "SBM" / TM / Sr.BDE
    BDE = "BDE"


class QualificationStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"
    UNATTRIBUTED = "UNATTRIBUTED"


class DisqualificationReason(str, Enum):
    """Only reasons that exist in the supplied business logic."""

    COUPON_UNDERUTILISED = "COUPON_UNDERUTILISED"
    INSUFFICIENT_OWN_SALES = "INSUFFICIENT_OWN_SALES"
    COUPON_NOT_FOUND = "COUPON_NOT_FOUND"
    PAYMENT_NOT_CAPTURED = "PAYMENT_NOT_CAPTURED"
    OUTSIDE_COUPON_WINDOW = "OUTSIDE_COUPON_WINDOW"
    DUPLICATE_PAYMENT_ID = "DUPLICATE_PAYMENT_ID"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class MonthStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    LOCKED = "LOCKED"


class SlabScope(str, Enum):
    """One scope per Policy lookup table."""

    BDE_MONTHLY = "BDE_MONTHLY"          # Policy!L12:M16
    SUBMANAGER_MONTHLY = "SUBMANAGER_MONTHLY"  # Policy!L18:M21
    TEAM_OWNER_QUARTERLY = "TEAM_OWNER_QUARTERLY"  # Policy!L23:M26
    ZM_NINE_MONTH = "ZM_NINE_MONTH"      # Policy!L28:M31
    FIRST_YEAR_MBBS_UNITS = "FIRST_YEAR_MBBS_UNITS"  # Policy!L5:M8


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------
class Employee(BaseModel):
    employee_id: str
    full_name: str
    initial: str | None = None
    email: str | None = None
    role: Role = Role.BDE
    designation: str | None = None       # raw workbook value: BDE / SBM / RM
    region: str | None = None            # e.g. "R5-Susmita"
    zone: str | None = None              # e.g. "Biswadip Sarkar"
    vertical: str = "Marrow"
    submanager_id: str | None = None
    rm_id: str | None = None
    zm_id: str | None = None
    business_head_id: str | None = None
    is_active: bool = True
    effective_from: date | None = None
    effective_to: date | None = None


class CouponSignature(BaseModel):
    """One row of `Coupon Working`. The grain is signature, not coupon code."""

    coupon_signature: str
    coupon_code: str                     # matches raw_sales.coupon
    employee_id: str
    initial: str | None = None
    region: str | None = None
    college_id: str | None = None
    discount_group: str | None = None
    group_size: str                      # G10 / G5 / G3
    required_sales: int
    min_sales: int
    activation_date: date | None = None
    deactivation_date: date | None = None

    @property
    def is_foundation(self) -> bool:
        """A foundation-course coupon. The workbook tests the coupon CODE for
        `FC`, not the discount group, so we do the same — the two agree on all
        1,229 August signatures but the code is what the formula reads."""
        return "FC" in (self.coupon_code or "")

    @property
    def is_fmge(self) -> bool:
        """FMGE coupons span many colleges, so college is dropped from their
        clubbing key (see `clubbing_key`)."""
        return "FMGE" in (self.discount_group or "")

    @property
    def clubbing_key(self) -> tuple:
        """Which signatures club together.

        Reproduces `Coupon Working`!O of the automated workbook:

            FMGE  : SUMIFS(Total, Code, Code, GroupSize, GroupSize, Activation, Activation)
            other : SUMIFS(Total, Code, Code, College, College, GroupSize, GroupSize,
                           Activation, Activation)

        An FMGE coupon's College ID is a comma-separated list of dozens of
        colleges, so matching on it would put every such coupon in its own
        group. Dropping it is what makes FMGE clubbing work.
        """
        if self.is_fmge:
            return (self.employee_id, self.group_size, self.activation_date)
        return (self.employee_id, self.college_id, self.group_size, self.activation_date)


class SalesTransaction(BaseModel):
    payment_id: str
    order_id: str | None = None
    invoice_id: str | None = None
    payment_status: str
    payment_date_ist: datetime
    paid_amount: float                   # gross, incl. GST
    net_amount: float                    # paid_amount * 100/118
    plan_id: int | None = None
    plan_title: str | None = None
    plan_duration_in_month: int | None = None
    course_id: int | None = None
    user_id: str | None = None
    college_id: str | None = None
    college_name: str | None = None
    coupon: str | None = None
    coupon_agent: str | None = None
    state: str | None = None


class Target(BaseModel):
    employee_id: str
    period: str                          # "2026-08"
    target_units: float
    winner_units: float
    vertical: str = "Marrow"
    status: str = "APPROVED"
    approved_by: str | None = None
    effective_from: date | None = None


# ---------------------------------------------------------------------------
# Engine outputs
# ---------------------------------------------------------------------------
class SignatureQualification(BaseModel):
    coupon_signature: str
    coupon_code: str
    employee_id: str
    is_foundation: bool
    total: int
    club_sales: int
    club_sales_fc: int
    min_sales: int
    qualification_1: bool
    qualification_2: bool
    qualification_3: bool                # binding
    min_own_sales: int = 0
    own_sales_met: bool = True
    overridden: bool = False
    override_reason: str | None = None


class TransactionQualification(BaseModel):
    payment_id: str
    employee_id: str | None
    coupon_signature: str | None
    status: QualificationStatus
    reason: DisqualificationReason | None = None
    reason_detail: str | None = None
    is_foundation: bool = False
    coupon_region: str | None = None
    # False for coupons belonging to a team outside the field scheme. The sale
    # is still recorded and still qualified; it just earns no field incentive.
    is_field: bool = True
    net_amount: float = 0.0
    paid_amount: float = 0.0


class SlabHit(BaseModel):
    """Audit trail for one slab lookup."""

    scope: SlabScope
    input_value: float
    threshold: float
    rate: float


class IncentiveBreakdown(BaseModel):
    """Everything Finance needs to audit one employee-month.

    Field names map 1:1 onto `Incentive Report- Output` columns; the column
    letter is in the description so an auditor can tie back to the workbook.
    """

    employee_id: str
    period: str
    designation: str | None = None
    region: str | None = None
    zone: str | None = None

    target_units: float = Field(0, description="col E")
    winner_units: float = Field(0, description="col F")
    gross_units: int = Field(0, description="col G")
    disqualified_units: int = Field(0, description="col H")
    foundation_units: int = Field(0, description="col I")
    achieved_units: int = Field(0, description="col J = G-H")
    unit_pct: float = Field(0, description="col K = J/E")

    target_revenue: float = Field(0, description="col L = E*ARPU")
    gross_revenue: float = Field(0, description="col M, incl. GST")
    gross_net_revenue: float = Field(0, description="net of GST, before disqualification")
    disqualified_revenue: float = Field(0, description="col N")
    qualified_revenue: float = Field(0, description="col O = net - N")
    revenue_pct: float = Field(0, description="col P = O/L")

    arpu: float = Field(0, description="ARPU!G = gross_net_revenue / gross_units")
    arpu_threshold: float = 33000.0
    arpu_rule_applied: str = ""          # "MAX(unit, revenue)" or "revenue only"
    base_pct: float = Field(0, description="col Q")

    incremental_pct: float = Field(0, description="col R")
    bde_rate: float = 0.0
    bde_incentive: float = Field(0, description="col S")

    submanager_target_revenue: float = Field(0, description="col T")
    submanager_achieved_revenue: float = Field(0, description="col U")
    submanager_pct: float = Field(0, description="col V")
    submanager_rate: float = 0.0
    submanager_incentive: float = Field(0, description="col W")

    rm_incentive: float = Field(0, description="col AA, quarterly pro-rata")
    other_incentive: float = Field(0, description="col AB")
    prior_period_adjustment: float = Field(0, description="col AC")
    coupon_penalty: float = Field(0, description="col AN")

    total_incentive: float = Field(0, description="col AF = S+W+AB+AC")
    accumulation: float = Field(0, description="col AD, above cap")
    net_payable: float = Field(0, description="col AE = AF-AD")

    is_active: bool = True               # false => zero sales => treated as exit
    slab_hits: list[SlabHit] = Field(default_factory=list)
    calculation_version: int = 1
    calculated_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)


class UploadSummary(BaseModel):
    batch_id: str
    filename: str
    uploaded_by: str
    uploaded_at: datetime
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    duplicate_rows: int = 0
    unknown_initials: int = 0
    unknown_coupons: int = 0
    qualified_revenue: float = 0.0
    disqualified_revenue: float = 0.0
    validation_status: str = "PENDING"
    errors: list[dict] = Field(default_factory=list)


class ValidationError(BaseModel):
    row_number: int
    payment_id: str | None = None
    field: str
    error_code: str
    message: str
