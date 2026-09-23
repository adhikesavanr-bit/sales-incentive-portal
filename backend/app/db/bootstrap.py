"""Bootstrap: load the existing spreadsheets into BigQuery.

Turns the files Finance already maintains into the portal's master data, so a
fresh deployment has something real to run against.

    # see what it would do, touching nothing
    python -m app.db.bootstrap --source /path/to/files --period 2026-08 --dry-run

    # write it
    python -m app.db.bootstrap --source /path/to/files --period 2026-08
    python -m app.db.bootstrap --source /path/to/files --period 2026-08 --with-sales

Expected files in --source:
    Coupon_Agent_Details.xlsx          initial -> employee id, name, region
    Emp_email_ID_list.xlsx             employee id -> official email
    Field_Incentive_Report_<M>.xlsx    targets and reporting lines
    Coupon_Working_<M>.xlsx            coupon master
    Big_Query_Table_Sales_Data.csv     raw sales (only with --with-sales)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.engines.qualification import (
    MIN_SALES_BY_GROUP_SIZE,
    REQUIRED_SALES_BY_GROUP_SIZE,
    min_sales_for,
)
from app.models.schemas import Role

SEEDS = Path(__file__).with_name("seeds")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _norm(name: str) -> str:
    """Names differ across the files — 'Nikhil D' vs 'Nikhil', 'Amey jadhav'
    vs 'Amey Jadhav'. Employee id is the only join key we trust, so this is
    used solely to resolve manager NAMES back to ids."""
    return re.sub(r"[^a-z ]", "", str(name).lower()).strip()


def _find(source: Path, pattern: str) -> Path:
    matches = sorted(source.glob(pattern))
    if not matches:
        raise SystemExit(
            f"No file matching '{pattern}' in {source}. "
            "Check --source points at the folder holding the source workbooks."
        )
    return matches[-1]


def _resolve(name: str, by_name: dict[str, str]) -> str | None:
    """Match a manager's name to an employee id, tolerating spelling drift."""
    n = _norm(name)
    if not n:
        return None
    if n in by_name:
        return by_name[n]
    # first + last token, then first token alone
    parts = n.split()
    for candidate, emp in by_name.items():
        c = candidate.split()
        if parts[0] == c[0] and (len(parts) == 1 or len(c) == 1 or parts[-1] == c[-1]):
            return emp
    return None


# ---------------------------------------------------------------------------
# extractors
# ---------------------------------------------------------------------------
def read_agents(source: Path) -> pd.DataFrame:
    df = pd.read_excel(_find(source, "Coupon_Agent_Details*.xlsx"))
    df = df.rename(columns={
        "BDE Initial": "initial", "BDE Name": "full_name",
        "Employee IDs": "employee_id", "Zone": "region",
    })
    df = df[df["employee_id"].astype(str).str.match(r"NH[PCI]\d+", na=False)]
    # '#N/A' region means the agent is not mapped to a region yet.
    df["region"] = df["region"].where(df["region"].astype(str).str.startswith("R"), None)
    return df[["initial", "full_name", "employee_id", "region"]].drop_duplicates("employee_id")


def read_emails(source: Path) -> dict[str, str]:
    df = pd.read_excel(_find(source, "Emp_email_ID_list*.xlsx"))
    col = {c.lower().strip(): c for c in df.columns}
    eid = col.get("employee id")
    mail = next((v for k, v in col.items() if "email" in k), None)
    if not eid or not mail:
        raise SystemExit("Emp_email_ID_list needs an 'Employee ID' and an email column.")
    return {
        str(r[eid]).strip(): str(r[mail]).strip().lower()
        for _, r in df.iterrows()
        if pd.notna(r[eid]) and pd.notna(r[mail])
    }


def read_target_block(source: Path, period: str) -> pd.DataFrame:
    """The monthly block of `Target Achieved- Input`, located by its title row."""
    path = _find(source, "Field_Incentive_Report*.xlsx")
    raw = pd.read_excel(path, sheet_name="Target Achieved- Input", header=None)
    month = datetime.strptime(period, "%Y-%m").strftime("%b %Y")
    titles = raw[raw[0].astype(str).str.contains(month, na=False)].index
    if len(titles) == 0:
        have = raw[0].astype(str)
        have = sorted({m for m in have if "Field Sales Targets" in m})
        raise SystemExit(
            f"No target block for {month} in {path.name}.\n"
            "Blocks present: " + ", ".join(have)
        )
    start = int(titles[0]) + 2
    t = raw.iloc[start:, 0:8].copy()
    t.columns = ["full_name", "employee_id", "submanager", "region", "rm", "zm",
                 "target_units", "winner_units"]
    t = t[t["employee_id"].astype(str).str.match(r"NH[PCI]\d+", na=False)]
    return t.reset_index(drop=True)


def read_coupons(source: Path) -> pd.DataFrame:
    path = _find(source, "Coupon_Working*.xlsx")
    df = pd.read_excel(path, sheet_name="Coupon Working")
    df = df.rename(columns={
        "coupon_signature": "coupon_signature", "Final coupons": "coupon_code",
        "Emp ID": "employee_id", "Code": "initial", "Zone": "region",
        "College ID": "college_id", "Discount Group": "discount_group",
        "Group Size": "group_size", "Required Sales": "required_sales",
        "Min Sales": "min_sales", "Activation Date": "activation_date",
        "Deactivation Date": "deactivation_date",
    })
    keep = ["coupon_signature", "coupon_code", "employee_id", "initial", "region",
            "college_id", "discount_group", "group_size", "required_sales",
            "min_sales", "activation_date", "deactivation_date"]
    df = df[[c for c in keep if c in df.columns]].dropna(subset=["coupon_signature"])

    # Derive anything the sheet omits, from the Slab table.
    if "group_size" not in df or df["group_size"].isna().any():
        df["group_size"] = df["discount_group"].str.extract(r"(G\d+)", expand=False)
    df["required_sales"] = df.apply(
        lambda r: int(r["required_sales"]) if pd.notna(r.get("required_sales"))
        else REQUIRED_SALES_BY_GROUP_SIZE.get(r["group_size"], 10), axis=1)
    df["min_sales"] = df.apply(
        lambda r: int(r["min_sales"]) if pd.notna(r.get("min_sales"))
        else min_sales_for(r["group_size"], r["required_sales"]), axis=1)
    return df


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------
def build(source: Path, period: str) -> dict[str, list[dict]]:
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date().isoformat()

    agents = read_agents(source)
    emails = read_emails(source)
    targets_raw = read_target_block(source, period)
    bh = json.loads((SEEDS / "business_heads.json").read_text())
    default_bh = bh.get("default_business_head_id")

    # employee id -> name, from every file that names people
    by_name: dict[str, str] = {}
    for _, r in agents.iterrows():
        by_name[_norm(r["full_name"])] = r["employee_id"]
    for _, r in targets_raw.iterrows():
        by_name.setdefault(_norm(r["full_name"]), r["employee_id"])

    # --- employees -------------------------------------------------------
    people: dict[str, dict] = {}
    for _, r in agents.iterrows():
        people[r["employee_id"]] = {
            "employee_id": r["employee_id"],
            "full_name": str(r["full_name"]).strip(),
            "initial": r["initial"],
            "email": emails.get(r["employee_id"]),
            "role": Role.BDE.value,
            "designation": "BDE",
            "region": r["region"],
            "zone": None,
            "vertical": "Marrow",
            "is_active": True,
            "effective_from": today,
            "effective_to": None,
            "updated_at": now,
            "updated_by": "bootstrap",
        }

    unresolved: list[str] = []
    hierarchy: list[dict] = []
    targets: list[dict] = []

    for _, r in targets_raw.iterrows():
        eid = r["employee_id"]
        p = people.setdefault(eid, {
            "employee_id": eid, "full_name": str(r["full_name"]).strip(),
            "initial": None, "email": emails.get(eid), "role": Role.BDE.value,
            "designation": "BDE", "region": None, "zone": None,
            "vertical": "Marrow", "is_active": True, "effective_from": today,
            "effective_to": None, "updated_at": now, "updated_by": "bootstrap",
        })
        p["region"] = r["region"] if pd.notna(r["region"]) else p["region"]
        p["zone"] = str(r["zm"]).strip() if pd.notna(r["zm"]) else None
        if not p["email"]:
            p["email"] = emails.get(eid)

        sub = _resolve(r["submanager"], by_name) if pd.notna(r["submanager"]) else None
        rm = _resolve(r["rm"], by_name) if pd.notna(r["rm"]) else None
        zm = _resolve(r["zm"], by_name) if pd.notna(r["zm"]) else None
        for label, raw_name in (("submanager", r["submanager"]), ("rm", r["rm"]), ("zm", r["zm"])):
            resolved = {"submanager": sub, "rm": rm, "zm": zm}[label]
            if pd.notna(raw_name) and not resolved:
                unresolved.append(f"{label}: {raw_name}")

        hierarchy.append({
            "employee_id": eid, "submanager_id": sub, "rm_id": rm, "zm_id": zm,
            "business_head_id": default_bh,
            "region": r["region"] if pd.notna(r["region"]) else None,
            "zone": str(r["zm"]).strip() if pd.notna(r["zm"]) else None,
            "effective_from": today, "effective_to": None,
            "updated_at": now, "updated_by": "bootstrap",
        })

        if pd.notna(r["target_units"]):
            base = float(r["target_units"])
            targets.append({
                "employee_id": eid, "period": period, "vertical": "Marrow",
                "target_units": base,
                "winner_units": float(r["winner_units"]) if pd.notna(r["winner_units"])
                else round(base * 1.3, 2),
                "status": "APPROVED", "submitted_by": "bootstrap",
                "approved_by": "bootstrap", "approved_at": now,
                "effective_from": today, "version": 1, "updated_at": now,
            })

    # Promote anyone who manages someone.
    managed = {("submanager", h["submanager_id"]) for h in hierarchy if h["submanager_id"]}
    managed |= {("rm", h["rm_id"]) for h in hierarchy if h["rm_id"]}
    managed |= {("zm", h["zm_id"]) for h in hierarchy if h["zm_id"]}
    role_for = {"submanager": Role.SUB_MANAGER, "rm": Role.REGIONAL_MANAGER,
                "zm": Role.ZONAL_MANAGER}
    rank = {Role.BDE: 0, Role.SUB_MANAGER: 1, Role.REGIONAL_MANAGER: 2,
            Role.ZONAL_MANAGER: 3}
    for level, eid in managed:
        p = people.get(eid)
        if not p:
            continue
        new = role_for[level]
        if rank[new] > rank[Role(p["role"])]:
            p["role"] = new.value

    # Business heads from the seed file.
    for head in bh["business_heads"]:
        people[head["employee_id"]] = {
            **head, "initial": None, "designation": "Business Head",
            "region": None, "zone": None, "effective_from": today,
            "effective_to": None, "updated_at": now, "updated_by": "bootstrap",
        }
        hierarchy.append({
            "employee_id": head["employee_id"], "submanager_id": None,
            "rm_id": None, "zm_id": None, "business_head_id": None,
            "region": None, "zone": None, "effective_from": today,
            "effective_to": None, "updated_at": now, "updated_by": "bootstrap",
        })

    # --- coupons ---------------------------------------------------------
    coupons = read_coupons(source)
    coupon_rows = []
    for _, r in coupons.iterrows():
        row = {k: (None if pd.isna(v) else v) for k, v in r.items()}
        for d in ("activation_date", "deactivation_date"):
            if row.get(d) is not None:
                row[d] = pd.to_datetime(row[d]).date().isoformat()
        row["upload_batch_id"] = "BOOTSTRAP"
        row["ingested_at"] = now
        coupon_rows.append(row)

    return {
        "employee_master": list(people.values()),
        "reporting_hierarchy": hierarchy,
        "targets": targets,
        "coupon_master": coupon_rows,
        "_unresolved": sorted(set(unresolved)),
    }


def build_sales(source: Path, period: str) -> list[dict]:
    from app.services.upload import COLUMN_MAP

    path = _find(source, "Big_Query_Table_Sales_Data*.csv")
    df = pd.read_csv(path, low_memory=False)
    df = df.rename(columns=COLUMN_MAP)
    keep = [c for c in COLUMN_MAP.values() if c in df.columns]
    df = df[keep]

    df["payment_date_ist"] = pd.to_datetime(df["payment_date_ist"], utc=True, errors="coerce")
    df = df[df["payment_date_ist"].dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m") == period]

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in df.where(pd.notna(df), None).to_dict("records"):
        for d in ("payment_date", "payment_date_ist", "subscription_date",
                  "subscription_date_ist", "termination_date"):
            if r.get(d) is not None:
                r[d] = str(r[d])
        r.update(upload_batch_id="BOOTSTRAP", source_file=path.name,
                 ingested_at=now, ingested_by="bootstrap")
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--with-sales", action="store_true",
                    help="also load raw_sales for the period")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, touch nothing")
    a = ap.parse_args()

    if not re.match(r"^\d{4}-\d{2}$", a.period):
        raise SystemExit("--period must look like 2026-08")

    data = build(a.source, a.period)
    unresolved = data.pop("_unresolved")

    print(f"Bootstrap for {a.period} from {a.source}\n")
    for table, rows in data.items():
        print(f"  {table:22s} {len(rows):>6,} rows")
    sales: list[dict] = []
    if a.with_sales:
        sales = build_sales(a.source, a.period)
        print(f"  {'raw_sales':22s} {len(sales):>6,} rows")

    missing_email = [p["employee_id"] for p in data["employee_master"] if not p["email"]]
    if missing_email:
        print(f"\n  {len(missing_email)} employees have no email and cannot sign in:")
        print("   ", ", ".join(missing_email[:15]) + (" ..." if len(missing_email) > 15 else ""))
    if unresolved:
        print(f"\n  {len(unresolved)} manager names could not be matched to an employee id:")
        for u in unresolved[:10]:
            print("   ", u)
        print("    Fix the spelling in the target sheet, or add the person to the agent list.")

    if a.dry_run:
        print("\nDry run. Nothing written.")
        return 0

    from app.db import bigquery as bq

    for table, rows in data.items():
        bq.load_rows(table, rows)
        print(f"  wrote {len(rows):,} -> {table}")
    if sales:
        bq.load_rows("raw_sales", sales)
        print(f"  wrote {len(sales):,} -> raw_sales")

    print("\nDone. Next: POST /api/incentive/calculate?period=" + a.period)
    return 0


if __name__ == "__main__":
    sys.exit(main())
