"""Reconciliation harness: run the engines against the supplied August 2026
workbook and compare, line by line, with `Incentive Report- Output`.

This is not a unit test — it needs the source workbooks. It is the acceptance
gate for any change to the engines.

    python -m tests.reconcile_august /mnt/user-data/uploads
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.incentive import (  # noqa: E402
    IncentiveConfig,
    calculate_period,
)
from app.engines.qualification import (  # noqa: E402
    QualificationOverride,
    run_qualification,
)
from app.source_files import require as require_sources  # noqa: E402
from app.models.schemas import (  # noqa: E402
    CouponSignature,
    Employee,
    SalesTransaction,
    Target,
)

PERIOD = "2026-08"
AUG_BLOCK_START = 385  # row of the August header in `Incentive Report- Output`

FILES: dict[str, Path] = {}


def load_signatures(path: Path) -> list[CouponSignature]:
    cw = pd.read_excel(FILES["coupon_working"], sheet_name="Coupon Working")
    out = []
    for _, r in cw.iterrows():
        out.append(
            CouponSignature(
                coupon_signature=r["coupon_signature"],
                coupon_code=r["Final coupons"],
                employee_id=r["Emp ID"],
                initial=r["Code"],
                region=r["Zone"],
                college_id=str(r["College ID"]),
                discount_group=r["Discount Group"],
                group_size=r["Group Size"],
                required_sales=int(r["Required Sales"]),
                min_sales=int(r["Min Sales"]),
                activation_date=pd.to_datetime(r["Activation Date"]).date()
                if pd.notna(r["Activation Date"]) else None,
                deactivation_date=pd.to_datetime(r["Deactivation Date"]).date()
                if pd.notna(r["Deactivation Date"]) else None,
            )
        )
    return out


def load_transactions(path: Path) -> list[SalesTransaction]:
    md = pd.read_excel(FILES["coupon_working"], sheet_name="Master Data")
    out = []
    for _, r in md.iterrows():
        out.append(
            SalesTransaction(
                payment_id=r["payment_id"],
                payment_status=r["payment_status"],
                payment_date_ist=pd.to_datetime(r["payment_date"]),
                paid_amount=float(r["paid_amount"]),
                net_amount=float(r["paid_amount_*_100/118"]),
                college_id=str(r["college_id"]) if pd.notna(r["college_id"]) else None,
                coupon=r["coupon"],
                coupon_agent=r["coupon_agent"] if pd.notna(r["coupon_agent"]) else None,
                plan_title=r["Plan_Title"] if pd.notna(r["Plan_Title"]) else None,
            )
        )
    return out


def load_report(path: Path) -> pd.DataFrame:
    """The August block of `Incentive Report- Output`, columns A..AF."""
    raw = pd.read_excel(
        FILES["incentive_report"], sheet_name="Incentive Report- Output", header=None
    )
    hdr = AUG_BLOCK_START + 2  # header row (1-based 387) -> 0-based 386
    block = raw.iloc[hdr:hdr + 95].copy()
    cols = {
        0: "name", 1: "employee_id", 2: "designation", 3: "region",
        4: "target_units", 6: "gross_units", 7: "disqualified_units",
        9: "achieved_units", 10: "unit_pct", 11: "target_revenue",
        12: "gross_revenue", 13: "disqualified_revenue", 14: "qualified_revenue",
        15: "revenue_pct", 16: "base_pct", 18: "bde_incentive",
        22: "submanager_incentive", 31: "total_incentive",
    }
    block = block[list(cols)].rename(columns=cols)
    block = block[block["employee_id"].astype(str).str.startswith("NHP")]
    for c in cols.values():
        if c not in ("name", "employee_id", "designation", "region"):
            block[c] = pd.to_numeric(block[c], errors="coerce")
    return block.reset_index(drop=True)


def load_targets_and_employees(path: Path) -> tuple[dict, dict]:
    """August targets: `Target Achieved- Input` rows 374-466, columns A-H.

    The block is located by the header row `Troopers | Employee IDs | ...`
    that follows the `Field Sales Targets Aug 2026-27` title, matching the
    SUMIF range in `Incentive Report- Output`!E388.
    """
    raw = pd.read_excel(
        FILES["incentive_report"], sheet_name="Target Achieved- Input", header=None
    )
    title = raw[raw[0].astype(str).str.contains("Aug 2026", na=False)].index
    start = int(title[0]) + 2  # skip title and header rows
    t = raw.iloc[start:, 0:8].copy()
    t.columns = ["name", "employee_id", "submanager", "region", "rm", "zm",
                 "base", "wc"]
    t = t[t["employee_id"].astype(str).str.startswith("NHP")]

    name_to_id = {
        str(r["name"]).strip(): r["employee_id"] for _, r in t.iterrows()
    }
    targets, employees = {}, {}
    for _, r in t.iterrows():
        eid = r["employee_id"]
        targets[eid] = Target(
            employee_id=eid,
            period=PERIOD,
            target_units=float(r["base"]) if pd.notna(r["base"]) else 0.0,
            winner_units=float(r["wc"]) if pd.notna(r["wc"]) else 0.0,
        )
        sub = str(r["submanager"]).strip() if pd.notna(r["submanager"]) else ""
        employees[eid] = Employee(
            employee_id=eid,
            full_name=str(r["name"]).strip(),
            region=r["region"] if pd.notna(r["region"]) else None,
            zone=r["zm"] if pd.notna(r["zm"]) else None,
            submanager_id=name_to_id.get(sub),
        )
    return targets, employees


def main(upload_dir: str) -> int:
    global FILES
    path = Path(upload_dir)
    FILES = require_sources(path, "coupon_working", "incentive_report")
    print("Loading source workbooks...")
    for key, p in FILES.items():
        print(f"  {key:18s} {p.name}")
    signatures = load_signatures(path)
    transactions = load_transactions(path)
    targets, employees = load_targets_and_employees(path)
    report = load_report(path)

    print(f"  {len(signatures):,} coupon signatures")
    print(f"  {len(transactions):,} transactions")
    print(f"  {len(targets):,} targets, {len(report):,} report rows\n")

    # --- qualification ----------------------------------------------------
    seed = json.loads(
        (Path(__file__).resolve().parents[1]
         / "app/db/seeds/qualification_overrides_2026_08.json").read_text()
    )
    overrides = [QualificationOverride(**o) for o in seed["overrides"]]
    print(f"  {len(overrides)} qualification overrides seeded\n")
    qres = run_qualification(
        transactions, signatures, qualification_overrides=overrides
    )
    expected = pd.read_excel(
        FILES["coupon_working"], sheet_name="Master Data"
    )[["payment_id", "Qualification"]]
    actual = pd.DataFrame(
        [{"payment_id": t.payment_id, "engine": t.status.value}
         for t in qres.transactions]
    )
    merged = expected.merge(actual, on="payment_id", how="left")
    merged["expected"] = merged["Qualification"].map(
        {"Qualified": "QUALIFIED", "Disqualified": "DISQUALIFIED"}
    )
    agree = (merged["engine"] == merged["expected"]).sum()
    print("QUALIFICATION ENGINE")
    print(f"  transactions matched : {agree:,} / {len(merged):,} "
          f"({agree / len(merged):.4%})")
    mism = merged[merged["engine"] != merged["expected"]]
    if len(mism):
        print(f"  mismatches           : {len(mism)}")
        print(mism.head(10).to_string(index=False))

    # --- incentive --------------------------------------------------------
    cfg = IncentiveConfig()
    breakdowns = calculate_period(
        qres.transactions, PERIOD, employees, targets, config=cfg
    )
    eng = pd.DataFrame([
        {
            "employee_id": b.employee_id,
            "e_gross_units": b.gross_units,
            "e_disq_units": b.disqualified_units,
            "e_achieved_units": b.achieved_units,
            "e_target_revenue": b.target_revenue,
            "e_disq_revenue": b.disqualified_revenue,
            "e_qualified_revenue": b.qualified_revenue,
            "e_base_pct": b.base_pct,
            "e_bde_incentive": b.bde_incentive,
            "e_submanager_incentive": b.submanager_incentive,
            "e_total": b.total_incentive,
        }
        for b in breakdowns.values()
    ])
    cmp = report.merge(eng, on="employee_id", how="left")

    print("\nINCENTIVE ENGINE  (tolerance: 1 unit / Rs.1)")
    checks = [
        ("gross units", "gross_units", "e_gross_units", 0.5),
        ("disqualified units", "disqualified_units", "e_disq_units", 0.5),
        ("achieved units", "achieved_units", "e_achieved_units", 0.5),
        ("target revenue", "target_revenue", "e_target_revenue", 1.0),
        ("disqualified revenue", "disqualified_revenue", "e_disq_revenue", 1.0),
        ("qualified revenue", "qualified_revenue", "e_qualified_revenue", 1.0),
        ("base %", "base_pct", "e_base_pct", 0.0001),
        ("BDE incentive", "bde_incentive", "e_bde_incentive", 1.0),
        ("total incentive", "total_incentive", "e_total", 1.0),
    ]
    for label, a, b, tol in checks:
        d = (cmp[a].fillna(0) - cmp[b].fillna(0)).abs()
        ok = (d <= tol).sum()
        print(f"  {label:22s} {ok:3d} / {len(cmp):3d} within tolerance"
              f"   max delta {d.max():,.2f}")

    print(f"\n  workbook total incentive : "
          f"Rs. {report['total_incentive'].sum():,.2f}")
    print(f"  engine   total incentive : "
          f"Rs. {eng['e_total'].sum():,.2f}")

    worst = cmp.assign(
        d=(cmp["bde_incentive"].fillna(0) - cmp["e_bde_incentive"].fillna(0)).abs()
    ).nlargest(8, "d")[
        ["name", "employee_id", "designation", "target_units",
         "bde_incentive", "e_bde_incentive", "d"]
    ]
    print("\n  largest BDE-incentive deltas:")
    print(worst.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"))
