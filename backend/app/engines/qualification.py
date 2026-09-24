"""Qualification engine.

Reproduces the `Coupon Working` sheet: for each coupon signature it computes
Total, Club Sales, Club Sales FC and the three qualification tests, then pushes
the binding verdict (Qualification 3) down onto every transaction.

Pure functions, no IO, no database — so it can be unit-tested against the
supplied August workbook row for row. Verified at 100% on 16,012 payments.

See DATA_MAPPING.md §4.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.models.schemas import (
    CouponSignature,
    DisqualificationReason,
    QualificationStatus,
    SalesTransaction,
    SignatureQualification,
    TransactionQualification,
)

# Seed values from the `Slab` sheet of the automated workbook, read by
#     M = INDEX(Slab!B:B, MATCH(GroupSize, Slab!A:A, 0))
#
# A table, not ceil(0.8 * required): the 80% rule holds for G10 and up, but G5
# and G3 need full utilisation and G7 needs 5 of 7.
#
# These are DEFAULTS ONLY. At runtime the rules come from the `coupon_rules`
# table, effective-dated, so Finance can change a threshold without a code
# change and without disturbing a month that has already been paid.
MIN_SALES_BY_GROUP_SIZE: dict[str, int] = {
    "G3": 3,
    "G5": 5,
    "G7": 5,
    "G10": 8,
    "G15": 12,
    "G20": 16,
    "G25": 20,
}

REQUIRED_SALES_BY_GROUP_SIZE: dict[str, int] = {
    "G3": 3, "G5": 5, "G7": 7, "G10": 10, "G15": 15, "G20": 20, "G25": 25,
}

CAPTURED = "captured"

# Minimum sales a coupon must make ON ITS OWN before clubbing can carry it.
#
# From `Coupon Working`!S of the automated workbook:
#     =IF(AND(Q2="Yes", N2<5, P2=0), "No", Q2)
#
# So the floor is 5, and the waiver is P>0 — the clubbing GROUP contains
# foundation sales — not "this row is itself a foundation coupon". Twelve of
# the 51 coupons the waiver rescues in August are ordinary MM coupons sitting
# beside an FC coupon.
#
# G3 is the one size the formula does not cover: Finance confirmed that for G3
# the minimum is 3, which the August data bears out (8 G3 coupons with 3-4 own
# sales and no FC beside them, all qualified).
DEFAULT_MIN_OWN_SALES = 5
MIN_OWN_SALES_BY_GROUP_SIZE: dict[str, int] = {
    "G3": 3,
}


def min_own_sales_for(group_size: str) -> int:
    """The own-sales floor for a coupon, before the foundation waiver."""
    return MIN_OWN_SALES_BY_GROUP_SIZE.get(
        (group_size or "").upper(), DEFAULT_MIN_OWN_SALES
    )


@dataclass
class CouponPolicy:
    """The qualification thresholds in force for one period.

    Built from the `coupon_rules` table for the period being calculated, so a
    rule change today cannot alter what August was paid. Falls back to the
    seed values above when no rows exist, which is what a fresh install has.
    """

    min_sales: dict[str, int] = field(
        default_factory=lambda: dict(MIN_SALES_BY_GROUP_SIZE)
    )
    min_own_sales: dict[str, int] = field(
        default_factory=lambda: dict(MIN_OWN_SALES_BY_GROUP_SIZE)
    )
    default_min_own_sales: int = DEFAULT_MIN_OWN_SALES

    def min_sales_for(self, group_size: str, required_sales: int) -> int:
        size = (group_size or "").upper()
        if size in self.min_sales:
            return self.min_sales[size]
        return max(1, -(-required_sales * 8 // 10))  # ceil(0.8 * required)

    def min_own_sales_for(self, group_size: str) -> int:
        return self.min_own_sales.get(
            (group_size or "").upper(), self.default_min_own_sales
        )


@dataclass
class QualificationOverride:
    """A manual Finance decision on one signature. Always carries a reason."""

    coupon_signature: str
    qualified: bool
    reason: str
    approved_by: str
    approved_at: date | None = None


@dataclass
class ClubbingOverride:
    """Merges signatures into a single clubbing group by explicit instruction."""

    coupon_signatures: list[str]
    group_label: str
    reason: str
    approved_by: str


@dataclass
class QualificationResult:
    signatures: dict[str, SignatureQualification] = field(default_factory=dict)
    transactions: list[TransactionQualification] = field(default_factory=list)

    @property
    def qualified_revenue(self) -> float:
        return sum(t.net_amount for t in self.transactions
                   if t.status == QualificationStatus.QUALIFIED)

    @property
    def disqualified_revenue(self) -> float:
        return sum(t.net_amount for t in self.transactions
                   if t.status == QualificationStatus.DISQUALIFIED)


def min_sales_for(group_size: str, required_sales: int) -> int:
    """Minimum utilisation for a coupon group size.

    Falls back to the 80% rule for a group size absent from the Slab sheet, so
    a newly introduced size does not silently qualify at zero.
    """
    known = MIN_SALES_BY_GROUP_SIZE.get((group_size or "").upper())
    if known is not None:
        return known
    return max(1, -(-required_sales * 8 // 10))  # ceil(0.8 * required)


def _resolve_coupon(
    txn: SalesTransaction,
    by_code: dict[str, list[CouponSignature]],
) -> tuple[CouponSignature | None, DisqualificationReason | None]:
    """Pick the signature a payment belongs to.

    A coupon code can be issued in several windows, so the payment date selects
    the signature. If exactly one signature exists we use it regardless of
    window and flag the date mismatch separately, which mirrors the workbook:
    it matches on code alone.
    """
    sigs = by_code.get((txn.coupon or "").strip())
    if not sigs:
        return None, DisqualificationReason.COUPON_NOT_FOUND
    if len(sigs) == 1:
        return sigs[0], None

    d = txn.payment_date_ist.date()
    in_window = [
        s for s in sigs
        if (s.activation_date is None or s.activation_date <= d)
        and (s.deactivation_date is None or d <= s.deactivation_date)
    ]
    if in_window:
        # Latest activation wins when windows overlap.
        return max(in_window, key=lambda s: s.activation_date or date.min), None
    # Outside every window: attribute to the nearest preceding signature so the
    # sale is still counted against the right BDE, and record why.
    past = [s for s in sigs if s.activation_date and s.activation_date <= d]
    chosen = max(past, key=lambda s: s.activation_date) if past else sigs[0]
    return chosen, DisqualificationReason.OUTSIDE_COUPON_WINDOW


def run_qualification(
    transactions: list[SalesTransaction],
    signatures: list[CouponSignature],
    *,
    qualification_overrides: list[QualificationOverride] | None = None,
    clubbing_overrides: list[ClubbingOverride] | None = None,
    seen_payment_ids: set[str] | None = None,
    non_field_regions: set[str] | None = None,
    policy: CouponPolicy | None = None,
) -> QualificationResult:
    """Run the full qualification pass.

    Args:
        transactions: captured sales for the period.
        signatures: the coupon master for the period.
        qualification_overrides: explicit Finance decisions (DATA_MAPPING §4.2).
        clubbing_overrides: explicit clubbing merges (DATA_MAPPING §4.1).
        seen_payment_ids: payment ids already imported in an earlier batch.
        non_field_regions: coupon regions outside the field scheme. Their
            transactions are qualified and returned as normal, flagged
            `is_field=False`, so the coupon audit keeps them and the incentive
            calculation skips them.
        policy: the thresholds in force for this period. Defaults to the seed
            values, which are what the approved August 2026 workbook used.
    """
    rules = policy or CouponPolicy()
    non_field = {r.lower() for r in (non_field_regions or set())}
    q_over = {o.coupon_signature: o for o in (qualification_overrides or [])}
    seen = seen_payment_ids or set()

    by_code: dict[str, list[CouponSignature]] = defaultdict(list)
    by_signature: dict[str, CouponSignature] = {}
    for s in signatures:
        by_code[s.coupon_code].append(s)
        by_signature[s.coupon_signature] = s

    # --- pass 1: assign every transaction to a signature -------------------
    assigned: dict[str, list[SalesTransaction]] = defaultdict(list)
    txn_reason: dict[str, DisqualificationReason] = {}
    unattributed: list[tuple[SalesTransaction, DisqualificationReason]] = []
    batch_seen: set[str] = set()

    for txn in transactions:
        if txn.payment_id in seen or txn.payment_id in batch_seen:
            unattributed.append((txn, DisqualificationReason.DUPLICATE_PAYMENT_ID))
            continue
        batch_seen.add(txn.payment_id)

        if txn.payment_status != CAPTURED:
            unattributed.append((txn, DisqualificationReason.PAYMENT_NOT_CAPTURED))
            continue

        sig, reason = _resolve_coupon(txn, by_code)
        if sig is None:
            unattributed.append((txn, DisqualificationReason.COUPON_NOT_FOUND))
            continue
        assigned[sig.coupon_signature].append(txn)
        if reason is not None:
            txn_reason[txn.payment_id] = reason

    # --- pass 2: clubbing ---------------------------------------------------
    # Default keys come from the signature; an override re-labels the members.
    club_key: dict[str, tuple] = {
        sig_id: sig.clubbing_key for sig_id, sig in by_signature.items()
    }
    for ov in clubbing_overrides or []:
        for sig_id in ov.coupon_signatures:
            if sig_id in by_signature:
                club_key[sig_id] = ("OVERRIDE", ov.group_label)

    # Club Sales counts EVERY signature in the group, foundation ones included.
    # Club Sales FC counts only the foundation signatures in that same group.
    club_totals: dict[tuple, int] = defaultdict(int)
    fc_totals: dict[tuple, int] = defaultdict(int)
    for sig_id, sig in by_signature.items():
        n = len(assigned.get(sig_id, []))
        club_totals[club_key[sig_id]] += n
        if sig.is_foundation:
            fc_totals[club_key[sig_id]] += n

    # --- pass 3: the three tests -------------------------------------------
    result = QualificationResult()
    for sig_id, sig in by_signature.items():
        total = len(assigned.get(sig_id, []))
        # The coupon master carries a min_sales per signature, but the policy
        # in force for the period wins: it is the auditable, versioned source.
        min_sales = rules.min_sales_for(sig.group_size, sig.required_sales)

        # `Club Sales` counts every signature in the group, foundation ones
        # included; `Club Sales FC` counts only the foundation ones.
        club = club_totals[club_key[sig_id]]
        club_fc = fc_totals[club_key[sig_id]]

        # Q1: the coupon reached the minimum on its own.
        q1 = total >= min_sales
        # Q2: =IF(AND(Q1="No", Club>=Min), "Yes", Q1)
        q2 = q1 or club >= min_sales
        # Q3: =IF(AND(Q2="Yes", Total<floor, ClubFC=0), "No", Q2)
        #     A coupon that leant on the club must still have sold enough
        #     itself, unless the group includes foundation sales.
        own_floor = rules.min_own_sales_for(sig.group_size)
        own_ok = total >= own_floor or club_fc > 0
        q3 = q2 and own_ok

        overridden = False
        override_reason = None
        if sig_id in q_over:
            ov = q_over[sig_id]
            q3, overridden, override_reason = ov.qualified, True, ov.reason

        result.signatures[sig_id] = SignatureQualification(
            coupon_signature=sig_id,
            coupon_code=sig.coupon_code,
            employee_id=sig.employee_id,
            is_foundation=sig.is_foundation,
            total=total,
            club_sales=club,
            club_sales_fc=club_fc,
            min_sales=min_sales,
            qualification_1=q1,
            qualification_2=q2,
            qualification_3=q3,
            overridden=overridden,
            override_reason=override_reason,
            min_own_sales=own_floor,
            own_sales_met=own_ok,
        )

    # --- pass 4: push the verdict down onto transactions -------------------
    for sig_id, txns in assigned.items():
        sq = result.signatures[sig_id]
        sig = by_signature[sig_id]
        for txn in txns:
            if sq.qualification_3:
                status, reason, detail = QualificationStatus.QUALIFIED, None, None
            elif sq.overridden:
                status = QualificationStatus.DISQUALIFIED
                reason = DisqualificationReason.MANUAL_OVERRIDE
                detail = sq.override_reason
            elif not sq.own_sales_met:
                status = QualificationStatus.DISQUALIFIED
                reason = DisqualificationReason.INSUFFICIENT_OWN_SALES
                detail = (
                    f"Coupon {sq.coupon_code} made {sq.total} "
                    f"{'sale' if sq.total == 1 else 'sales'} of its own. "
                    f"A {sig.group_size} coupon needs at least {sq.min_own_sales} "
                    f"before clubbing can count towards it, unless a foundation "
                    f"coupon was sold alongside it."
                )
            else:
                status = QualificationStatus.DISQUALIFIED
                reason = DisqualificationReason.COUPON_UNDERUTILISED
                detail = (
                    f"Coupon {sq.coupon_code} reached {sq.club_sales + sq.club_sales_fc} "
                    f"clubbed sales against a minimum of {sq.min_sales} "
                    f"({sig.group_size})."
                )
            result.transactions.append(
                TransactionQualification(
                    payment_id=txn.payment_id,
                    employee_id=sig.employee_id,
                    coupon_signature=sig_id,
                    status=status,
                    reason=reason,
                    reason_detail=detail,
                    is_foundation=sig.is_foundation,
                    coupon_region=sig.region,
                    is_field=(sig.region or "").lower() not in non_field,
                    net_amount=txn.net_amount,
                    paid_amount=txn.paid_amount,
                )
            )

    for txn, reason in unattributed:
        result.transactions.append(
            TransactionQualification(
                payment_id=txn.payment_id,
                employee_id=None,
                coupon_signature=None,
                status=QualificationStatus.UNATTRIBUTED,
                reason=reason,
                reason_detail=(
                    f"Coupon '{txn.coupon}' is not in the coupon master."
                    if reason is DisqualificationReason.COUPON_NOT_FOUND
                    else f"Payment status is '{txn.payment_status}'."
                    if reason is DisqualificationReason.PAYMENT_NOT_CAPTURED
                    else "Payment id already imported in an earlier batch."
                ),
                net_amount=txn.net_amount,
                paid_amount=txn.paid_amount,
            )
        )

    return result
