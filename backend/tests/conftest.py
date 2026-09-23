"""Shared fixtures. Built to mirror the shapes seen in the August workbook."""
from __future__ import annotations

import os

# The engines are pure, but importing a service pulls in Settings. Give the
# suite a self-contained configuration so no test ever reaches a real project.
os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("BQ_DATASET", "test_dataset")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")

from datetime import date, datetime

import pytest

from app.models.schemas import CouponSignature, Employee, Role, SalesTransaction, Target

AUG = date(2026, 8, 8)


def sig(code, emp, college, size="G10", group="1_5_FIELDG10", act=AUG):
    from app.engines.qualification import min_sales_for
    required = {"G10": 10, "G5": 5, "G3": 3}[size]
    return CouponSignature(
        coupon_signature=f"{code}_{act:%y%m%d}-10:00_{group}",
        coupon_code=code,
        employee_id=emp,
        college_id=college,
        discount_group=group,
        group_size=size,
        required_sales=required,
        min_sales=min_sales_for(size, required),
        activation_date=act,
        deactivation_date=date(2026, 8, 31),
    )


def txn(pid, coupon, amount=35999.0, day=10, status="captured"):
    return SalesTransaction(
        payment_id=pid,
        payment_status=status,
        payment_date_ist=datetime(2026, 8, day, 12, 0, 0),
        paid_amount=amount,
        net_amount=round(amount * 100 / 118, 6),
        coupon=coupon,
    )


def sales(coupon, n, prefix="p", amount=35999.0):
    return [txn(f"{prefix}-{coupon}-{i}", coupon, amount) for i in range(n)]


@pytest.fixture
def employees() -> dict[str, Employee]:
    return {
        "NHP001": Employee(employee_id="NHP001", full_name="A BDE",
                           email="a@marrowmed.com", role=Role.BDE,
                           designation="BDE", region="R1", zone="Z1",
                           submanager_id="NHP002", rm_id="NHP003", zm_id="NHP004"),
        "NHP002": Employee(employee_id="NHP002", full_name="B SubManager",
                           email="b@marrowmed.com", role=Role.SUB_MANAGER,
                           designation="SBM", region="R1", zone="Z1",
                           rm_id="NHP003", zm_id="NHP004"),
        "NHP003": Employee(employee_id="NHP003", full_name="C RM",
                           email="c@marrowmed.com", role=Role.REGIONAL_MANAGER,
                           designation="RM", region="R1", zone="Z1", zm_id="NHP004"),
        "NHP004": Employee(employee_id="NHP004", full_name="D ZM",
                           email="d@marrowmed.com", role=Role.ZONAL_MANAGER,
                           designation="ZM", zone="Z1"),
    }


@pytest.fixture
def targets() -> dict[str, Target]:
    return {
        "NHP001": Target(employee_id="NHP001", period="2026-08",
                         target_units=100, winner_units=130),
        "NHP002": Target(employee_id="NHP002", period="2026-08",
                         target_units=50, winner_units=65),
    }
