"""Qualification engine. Every case is drawn from a real August pattern."""
from __future__ import annotations

import pytest

from app.engines.qualification import (
    QualificationOverride,
    min_sales_for,
    run_qualification,
)
from app.models.schemas import DisqualificationReason, QualificationStatus
from tests.conftest import sales, sig, txn


class TestMinSales:
    """From the Slab sheet: 80% for G10 and up, 100% for G5 and G3, 5-of-7 for G7."""

    @pytest.mark.parametrize("size,required,expected", [
        ("G3", 3, 3), ("G5", 5, 5), ("G7", 7, 5), ("G10", 10, 8),
        ("G15", 15, 12), ("G20", 20, 16), ("G25", 25, 20),
    ])
    def test_every_size_in_the_slab_sheet(self, size, required, expected):
        assert min_sales_for(size, required) == expected

    def test_unknown_group_size_falls_back_to_80_percent(self):
        # A size not yet in the Slab sheet must not qualify at zero.
        assert min_sales_for("G50", 50) == 40


class TestQualification1:
    def test_coupon_meeting_its_own_minimum_qualifies(self):
        s = sig("AAA01MMA", "NHP001", "COL1")
        res = run_qualification(sales("AAA01MMA", 8), [s])
        sq = res.signatures[s.coupon_signature]
        assert (sq.total, sq.qualification_1, sq.qualification_3) == (8, True, True)
        assert all(t.status is QualificationStatus.QUALIFIED for t in res.transactions)

    def test_coupon_below_minimum_is_disqualified(self):
        """Own sales clear the floor, but nothing clubs in to reach 8."""
        s = sig("AAA02MMB", "NHP001", "COL1")
        res = run_qualification(sales("AAA02MMB", 5), [s])
        sq = res.signatures[s.coupon_signature]
        assert sq.qualification_1 is False and sq.qualification_3 is False
        for t in res.transactions:
            assert t.status is QualificationStatus.DISQUALIFIED
            assert t.reason is DisqualificationReason.COUPON_UNDERUTILISED
            assert "minimum of 8" in t.reason_detail


class TestClubbing:
    def test_siblings_at_one_college_club_together(self):
        """Pattern from MNS17MMA/MNS17MMD: neither reaches 8 alone, together they do.

        The second coupon clears the own-sales floor of 5, so clubbing is
        allowed to carry it the rest of the way.
        """
        a = sig("BBB01MMA", "NHP001", "COL9")
        b = sig("BBB01MMD", "NHP001", "COL9")
        res = run_qualification(
            sales("BBB01MMA", 6) + sales("BBB01MMD", 5), [a, b]
        )
        assert res.signatures[a.coupon_signature].club_sales == 11
        assert res.signatures[b.coupon_signature].qualification_1 is False
        assert res.signatures[b.coupon_signature].qualification_3 is True
        assert all(t.status is QualificationStatus.QUALIFIED for t in res.transactions)

    def test_different_colleges_do_not_club(self):
        a = sig("CCC01MMA", "NHP001", "COL1")
        b = sig("CCC02MMB", "NHP001", "COL2")
        res = run_qualification(
            sales("CCC01MMA", 5) + sales("CCC02MMB", 5), [a, b]
        )
        assert res.signatures[a.coupon_signature].club_sales == 5
        assert res.signatures[a.coupon_signature].qualification_3 is False

    def test_different_agents_do_not_club(self):
        a = sig("DDD01MMA", "NHP001", "COL1")
        b = sig("EEE01MMA", "NHP002", "COL1")
        res = run_qualification(
            sales("DDD01MMA", 5) + sales("EEE01MMA", 5), [a, b]
        )
        assert res.signatures[a.coupon_signature].club_sales == 5


class TestOwnSalesFloor:
    """Clubbing cannot rescue a coupon that barely sold anything itself.

    From `Coupon Working`!S: IF(AND(Q2="Yes", Total<5, ClubFC=0), "No", Q2).
    The floor is 5, waived when the clubbing group contains foundation sales;
    G3 uses 3 instead.
    """

    def test_four_own_sales_on_a_g10_cannot_ride_a_large_club(self):
        """The SBK20MME pattern, now handled by rule rather than by override."""
        a = sig("KKK01MMA", "NHP001", "COL3")
        b = sig("KKK02MMB", "NHP001", "COL3")
        res = run_qualification(
            sales("KKK01MMA", 4) + sales("KKK02MMB", 23), [a, b]
        )
        sq = res.signatures[a.coupon_signature]
        assert sq.club_sales == 27          # clubbing comfortably clears 8
        assert sq.qualification_2 is True
        assert sq.qualification_3 is False  # but 4 own sales is below the floor
        t = next(t for t in res.transactions if t.coupon_signature == a.coupon_signature)
        assert t.reason is DisqualificationReason.INSUFFICIENT_OWN_SALES
        assert "at least 5" in t.reason_detail

    def test_five_own_sales_is_enough_to_be_carried(self):
        a = sig("LLL01MMA", "NHP001", "COL3")
        b = sig("LLL02MMB", "NHP001", "COL3")
        res = run_qualification(
            sales("LLL01MMA", 5) + sales("LLL02MMB", 23), [a, b]
        )
        assert res.signatures[a.coupon_signature].qualification_3 is True

    def test_an_ordinary_coupon_is_waived_by_a_foundation_coupon_beside_it(self):
        """The waiver is about the GROUP, not about the row.

        VBH24MMG is not a foundation coupon and made only 4 sales, but an FC
        coupon was sold at the same college in the same window, so the floor
        does not apply. Twelve August coupons qualify this way.
        """
        mm = sig("PPP01MMG", "NHP001", "COL2", size="G5", group="1_5_FIELDG5")
        nb = sig("PPP02MMH", "NHP001", "COL2", size="G5", group="1_5_FIELDG5")
        fc = sig("PPP01FCG", "NHP001", "COL2", size="G5", group="13_5_FIELDG5")
        res = run_qualification(
            sales("PPP01MMG", 4) + sales("PPP02MMH", 1) + sales("PPP01FCG", 1),
            [mm, nb, fc],
        )
        sq = res.signatures[mm.coupon_signature]
        assert sq.is_foundation is False
        assert sq.total == 4 and sq.club_sales == 6 and sq.club_sales_fc == 1
        assert sq.qualification_3 is True

    def test_the_waiver_needs_a_foundation_sale_not_merely_a_big_club(self):
        a = sig("QQQ01MMA", "NHP001", "COL2", size="G5", group="1_5_FIELDG5")
        b = sig("QQQ02MMB", "NHP001", "COL2", size="G5", group="1_5_FIELDG5")
        res = run_qualification(
            sales("QQQ01MMA", 4) + sales("QQQ02MMB", 20), [a, b]
        )
        sq = res.signatures[a.coupon_signature]
        assert sq.club_sales_fc == 0
        assert sq.qualification_3 is False

    def test_g3_is_satisfied_by_three_own_sales(self):
        s = sig("MMM01MMA", "NHP001", "COL4", size="G3", group="1_5_G3FMGE")
        res = run_qualification(sales("MMM01MMA", 3), [s])
        sq = res.signatures[s.coupon_signature]
        assert sq.min_own_sales == 3
        assert sq.qualification_3 is True

    def test_g3_with_one_own_sale_fails_even_on_a_club(self):
        """The URVFMAG26 pattern."""
        a = sig("NNN01MMA", "NHP001", "COL4", size="G3", group="1_5_G3FMGE")
        b = sig("NNN02MMB", "NHP001", "COL4", size="G3", group="1_5_G3FMGE")
        res = run_qualification(
            sales("NNN01MMA", 1) + sales("NNN02MMB", 8), [a, b]
        )
        assert res.signatures[a.coupon_signature].qualification_3 is False

    def test_foundation_coupons_waive_their_own_floor(self):
        """An FC coupon always contributes to Club Sales FC, so it waives the
        floor for itself as well as for its neighbours."""
        mm = sig("OOO01MMA", "NHP001", "COL6")
        fc = sig("OOO01FCA", "NHP001", "COL6", group="13_5_FIELDG10")
        res = run_qualification(sales("OOO01MMA", 9) + sales("OOO01FCA", 1), [mm, fc])
        sq = res.signatures[fc.coupon_signature]
        assert sq.club_sales_fc == 1
        assert sq.qualification_3 is True

    def test_fmge_coupons_club_without_matching_college(self):
        """FMGE College ID is a list of dozens of colleges, so it is dropped
        from the clubbing key — otherwise every FMGE coupon stands alone."""
        a = sig("RRR01MMA", "NHP001", "COL-A,COL-B,COL-C", size="G5",
                group="1_5_G5FMGE")
        b = sig("RRR02MMB", "NHP001", "COL-D,COL-E", size="G5", group="1_5_G5FMGE")
        res = run_qualification(sales("RRR01MMA", 5) + sales("RRR02MMB", 2), [a, b])
        assert res.signatures[b.coupon_signature].club_sales == 7
        assert res.signatures[b.coupon_signature].qualification_2 is True


class TestFoundationCourse:
    def test_fc_coupon_rides_on_the_main_club(self):
        """TSF23FCE: 1 own sale, qualified because TSF23MME sold 9 at the same college."""
        mm = sig("TSF23MME", "NHP001", "COL7")
        fc = sig("TSF23FCE", "NHP001", "COL7", group="13_5_FIELDG10")
        res = run_qualification(sales("TSF23MME", 9) + sales("TSF23FCE", 1), [mm, fc])
        sq = res.signatures[fc.coupon_signature]
        assert sq.is_foundation is True
        # Club Sales counts the whole group, foundation rows included: 9 + 1.
        assert (sq.total, sq.club_sales, sq.club_sales_fc) == (1, 10, 1)
        assert sq.qualification_1 is False
        assert sq.qualification_3 is True

    def test_fc_alone_does_not_reach_the_minimum(self):
        fc = sig("FFF01FCA", "NHP001", "COL8", group="13_5_FIELDG10")
        res = run_qualification(sales("FFF01FCA", 1), [fc])
        assert res.signatures[fc.coupon_signature].qualification_3 is False


class TestUnattributed:
    def test_unknown_coupon_is_never_guessed_onto_an_employee(self):
        s = sig("GGG01MMA", "NHP001", "COL1")
        res = run_qualification([txn("p1", "NOT-IN-MASTER")], [s])
        t = next(t for t in res.transactions if t.payment_id == "p1")
        assert t.status is QualificationStatus.UNATTRIBUTED
        assert t.employee_id is None
        assert t.reason is DisqualificationReason.COUPON_NOT_FOUND

    def test_non_captured_payment_is_excluded(self):
        s = sig("HHH01MMA", "NHP001", "COL1")
        res = run_qualification(
            [txn("p1", "HHH01MMA", status="refunded")], [s]
        )
        assert res.transactions[0].reason is DisqualificationReason.PAYMENT_NOT_CAPTURED

    def test_already_imported_payment_is_flagged_duplicate(self):
        s = sig("III01MMA", "NHP001", "COL1")
        res = run_qualification(
            sales("III01MMA", 8), [s], seen_payment_ids={"p-III01MMA-0"}
        )
        dupes = [t for t in res.transactions
                 if t.reason is DisqualificationReason.DUPLICATE_PAYMENT_ID]
        assert len(dupes) == 1

    def test_duplicate_within_one_file_is_caught(self):
        s = sig("JJJ01MMA", "NHP001", "COL1")
        rows = sales("JJJ01MMA", 8) + [txn("p-JJJ01MMA-0", "JJJ01MMA")]
        res = run_qualification(rows, [s])
        assert sum(1 for t in res.transactions
                   if t.reason is DisqualificationReason.DUPLICATE_PAYMENT_ID) == 1
        assert res.signatures[s.coupon_signature].total == 8


class TestOverride:
    def test_override_disqualifies_and_records_the_reason(self):
        """The SBK20MME pattern: 1 own sale on a club of 27, refused by Finance."""
        a = sig("SBK20MME", "NHP001", "COL5")
        b = sig("SBK21MMB", "NHP001", "COL5")
        res = run_qualification(
            sales("SBK20MME", 1) + sales("SBK21MMB", 26),
            [a, b],
            qualification_overrides=[QualificationOverride(
                coupon_signature=a.coupon_signature,
                qualified=False,
                reason="Clubbing refused: single sale on a large club",
                approved_by="finance@marrowmed.com",
            )],
        )
        sq = res.signatures[a.coupon_signature]
        assert sq.qualification_2 is True   # rule says yes
        assert sq.qualification_3 is False  # Finance says no
        assert sq.overridden is True
        t = next(t for t in res.transactions if t.coupon_signature == a.coupon_signature)
        assert t.reason is DisqualificationReason.MANUAL_OVERRIDE
        assert "Clubbing refused" in t.reason_detail


class TestNonFieldRegions:
    """A non-field coupon is still qualified — it just earns no field incentive."""

    def test_inside_sales_transactions_are_flagged_not_dropped(self):
        s = sig("RKR01MMA", "NHP819", "COL1")
        s.region = "Inside Sales"
        res = run_qualification(sales("RKR01MMA", 8), [s],
                                non_field_regions={"inside sales"})
        assert res.signatures[s.coupon_signature].qualification_3 is True
        assert len(res.transactions) == 8
        for t in res.transactions:
            assert t.status is QualificationStatus.QUALIFIED
            assert t.is_field is False
            assert t.coupon_region == "Inside Sales"

    def test_field_regions_stay_field(self):
        s = sig("PND01MMA", "NHP374", "COL1")
        s.region = "R1-Ajeet"
        res = run_qualification(sales("PND01MMA", 8), [s],
                                non_field_regions={"inside sales"})
        assert all(t.is_field for t in res.transactions)

    def test_matching_ignores_case(self):
        s = sig("RKR02MMA", "NHP819", "COL1")
        s.region = "INSIDE SALES"
        res = run_qualification(sales("RKR02MMA", 8), [s],
                                non_field_regions={"inside sales"})
        assert all(not t.is_field for t in res.transactions)

    def test_no_exclusions_configured_leaves_everything_field(self):
        s = sig("RKR03MMA", "NHP819", "COL1")
        s.region = "Inside Sales"
        res = run_qualification(sales("RKR03MMA", 8), [s])
        assert all(t.is_field for t in res.transactions)
