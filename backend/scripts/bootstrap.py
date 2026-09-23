"""Bootstrap a fresh dataset from the source workbooks.

Loads the master data that the app cannot derive from a sales file:
employee master, reporting hierarchy, coupon master, targets and the incentive
rule table. Run once after `python -m app.db.ddl --apply`.

    # see what would be loaded, without touching BigQuery or needing credentials
    python -m scripts.bootstrap --source /path/to/workbooks --dry-run

    # load it
    python -m scripts.bootstrap --source /path/to/workbooks --period 2026-08

Re-running is safe: every table is replaced for the periods being loaded.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.qualification import (  # noqa: E402
    MIN_SALES_BY_GROUP_SIZE,
    REQUIRED_SALES_BY_GROUP_SIZE,
    min_sales_for,
)
from app.models.schemas import Role  # noqa: E402
from app.source_files import require as require_sources  # noqa: E402

SEEDS = Path(__file__).resolve().parents[1] / "app/db/seeds"

# Resolved once, in main(), so every reader shares the same lookup.
FILES: dict[str, Path] = {}

# `Incentive Report- Output` column C -> our role vocabulary.
DESIGNATION_TO_ROLE = {
    "BDE": Role.BDE,
    "SBM": Role.SUB_MANAGER,
    "TM": Role.SUB_MANAGER,
    "SR BDE": Role.SUB_MANAGER,
    "RM": Role.REGIONAL_MANAGER,
    "ZM": Role.ZONAL_MANAGER,
}


def norm(name: str | None) -> str:
    """Names are spelt inconsistently across the source files.

    'Nikhil D' vs 'Nikhil', 'Amey jadhav' vs 'Amey Jadhav', 'Sumit Kumar' vs
    'Sumit Kumar Singh'. Employee ID is the only key used downstream; this
    normalisation exists solely to resolve the RM and ZM *names* in the target
    sheet back to an id.
    """
    return re.sub(r"[^a-z]", "", (name or "").lower())


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
def read_email_list(src: Path) -> pd.DataFrame:
    df = pd.read_excel(FILES["email_list"])
    df.columns = [str(c).strip() for c in df.columns]
    return df.rename(columns={
        "Employee ID": "employee_id",
        "Full Name (as per Aadhar)": "full_name",
        "Official Email ID": "email",
    })[["employee_id", "full_name", "email"]].dropna(subset=["employee_id"])


def read_agents(src: Path) -> pd.DataFrame:
    df = pd.read_excel(FILES["agent_details"])
    df = df.rename(columns={
        "BDE Initial": "initial",
        "BDE Name": "agent_name",
        "Employee IDs": "employee_id",
        "Zone": "region",
    })[["initial", "agent_name", "employee_id", "region"]]
    # '#N/A' appears in the Zone column for a handful of initials.
    df["region"] = df["region"].replace("#N/A", None)
    return df.dropna(subset=["employee_id"])


def read_target_block(src: Path, period: str) -> pd.DataFrame:
    """The month's block of `Target Achieved- Input`, columns A-H.

    Located by its title row, matching the SUMIF range the incentive report
    uses for that month.
    """
    raw = pd.read_excel(
        FILES["incentive_report"], sheet_name="Target Achieved- Input", header=None
    )
    month = datetime.strptime(period, "%Y-%m").strftime("%b %Y")
    hits = raw[raw[0].astype(str).str.contains(month, na=False)].index
    if len(hits) == 0:
        raise SystemExit(
            f"No target block titled '{month}' in Target Achieved- Input. "
            f"Check the period, or add the block to the workbook."
        )
    start = int(hits[0]) + 2
    t = raw.iloc[start:, 0:8].copy()
    t.columns = ["name", "employee_id", "submanager", "region", "rm", "zm",
                 "base", "wc"]
    t = t[t["employee_id"].astype(str).str.startswith("NHP")]
    return t.reset_index(drop=True)


def read_designations(src: Path, period: str) -> dict[str, str]:
    raw = pd.read_excel(
        FILES["incentive_report"], sheet_name="Incentive Report- Output", header=None
    )
    out: dict[str, str] = {}
    for _, r in raw.iterrows():
        eid = str(r[1])
        if eid.startswith("NHP") and pd.notna(r[2]):
            out[eid] = str(r[2]).strip()
    return out


def read_coupons(src: Path) -> pd.DataFrame:
    return pd.read_excel(FILES["coupon_working"], sheet_name="Coupon Working")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def build(src: Path, period: str) -> dict[str, list[dict]]:
    emails = read_email_list(src)
    agents = read_agents(src)
    targets_df = read_target_block(src, period)
    designations = read_designations(src, period)
    coupons = read_coupons(src)
    heads = json.loads((SEEDS / "business_heads.json").read_text())
    head_id = heads["default_business_head_id"]
    role_overrides = json.loads((SEEDS / "role_overrides.json").read_text())["roles"]

    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()

    # --- name -> id, for resolving the RM and ZM columns ------------------
    name_to_id: dict[str, str] = {}
    for _, r in emails.iterrows():
        name_to_id.setdefault(norm(r["full_name"]), r["employee_id"])
    for _, r in agents.iterrows():
        name_to_id.setdefault(norm(r["agent_name"]), r["employee_id"])
    for _, r in targets_df.iterrows():
        name_to_id.setdefault(norm(r["name"]), r["employee_id"])

    email_by_id = dict(zip(emails["employee_id"], emails["email"]))
    name_by_id = dict(zip(emails["employee_id"], emails["full_name"]))
    initial_by_id = dict(zip(agents["employee_id"], agents["initial"]))

    warnings: list[str] = []

    def warn(msg: str) -> None:
        if msg not in warnings:
            warnings.append(msg)

    def resolve(label) -> tuple[str | None, str | None]:
        """Turn an RM/ZM cell into (employee_id, zone_or_region_label).

        Most cells hold a person's name. Some hold a placeholder such as
        'Z5-Independent(Anjaney)' or 'Independent(APTS)', which marks a zone
        that has no manager of its own — those resolve to a label with no id.
        """
        if pd.isna(label):
            return None, None
        text = str(label).strip()
        if "Independent" in text or re.match(r"^[RZ]\d+-", text):
            return None, text
        return name_to_id.get(norm(text)), text

    # --- employee master + hierarchy -------------------------------------
    employees: dict[str, dict] = {}
    hierarchy: list[dict] = []

    for _, r in targets_df.iterrows():
        eid = r["employee_id"]
        sub = name_to_id.get(norm(r["submanager"])) if pd.notna(r["submanager"]) else None
        rm, rm_label = resolve(r["rm"])
        zm, zone_label = resolve(r["zm"])
        if rm_label and rm is None and "Independent" not in rm_label:
            warn(f"RM '{rm_label}' (region {r['region']}) is not in the employee master — region left without an RM")
        if zone_label and zm is None and "Independent" not in zone_label:
            warn(f"ZM '{zone_label}' is not in the employee master — zone left without a ZM")
        if zone_label and "Independent" in zone_label:
            warn(f"Zone '{zone_label}' has no zonal manager; its regions report straight to the business head")
        designation = designations.get(eid, "BDE")
        role = DESIGNATION_TO_ROLE.get(designation.upper(), Role.BDE)
        email = email_by_id.get(eid)
        if not email:
            warnings.append(f"{eid} ({r['name']}) has no email in Emp_email_ID_list — cannot sign in")

        employees[eid] = {
            "employee_id": eid,
            "full_name": name_by_id.get(eid) or str(r["name"]).strip(),
            "initial": initial_by_id.get(eid),
            "email": email,
            "role": role.value,
            "designation": designation,
            "region": r["region"] if pd.notna(r["region"]) else None,
            "zone": zone_label,
            "vertical": "Marrow",
            "is_active": True,
            "effective_from": today,
            "effective_to": None,
            "updated_at": now,
            "updated_by": "bootstrap",
        }

        hierarchy.append({
            "employee_id": eid,
            # Never let anyone be their own manager: an RM appears in the
            # target sheet as both a row and the RM of that row.
            "submanager_id": sub if sub != eid else None,
            "rm_id": rm if rm != eid else None,
            "zm_id": zm if zm != eid else None,
            "business_head_id": head_id,
            "region": r["region"] if pd.notna(r["region"]) else None,
            "zone": zone_label,
            "effective_from": today,
            "effective_to": None,
            "updated_at": now,
            "updated_by": "bootstrap",
        })

    # RMs and ZMs appear in the target sheet only as a name in a column, so
    # they have no row of their own. Create one from the email list, otherwise
    # they cannot sign in and their subtree is invisible to them.
    for h in hierarchy:
        for mgr_id, mgr_role in ((h["rm_id"], Role.REGIONAL_MANAGER),
                                 (h["zm_id"], Role.ZONAL_MANAGER)):
            if not mgr_id or mgr_id in employees:
                continue
            employees[mgr_id] = {
                "employee_id": mgr_id,
                "full_name": name_by_id.get(mgr_id, mgr_id),
                "initial": initial_by_id.get(mgr_id),
                "email": email_by_id.get(mgr_id),
                "role": mgr_role.value,
                "designation": "RM" if mgr_role is Role.REGIONAL_MANAGER else "ZM",
                "region": h["region"] if mgr_role is Role.REGIONAL_MANAGER else None,
                "zone": h["zone"],
                "vertical": "Marrow",
                "is_active": True,
                "effective_from": today,
                "effective_to": None,
                "updated_at": now,
                "updated_by": "bootstrap",
            }
            hierarchy.append({
                "employee_id": mgr_id,
                "submanager_id": None,
                "rm_id": None,
                "zm_id": h["zm_id"] if mgr_role is Role.REGIONAL_MANAGER else None,
                "business_head_id": head_id,
                "region": h["region"] if mgr_role is Role.REGIONAL_MANAGER else None,
                "zone": h["zone"],
                "effective_from": today,
                "effective_to": None,
                "updated_at": now,
                "updated_by": "bootstrap",
            })
            if not email_by_id.get(mgr_id):
                warn(f"{mgr_id} manages a team but has no email — cannot sign in")

    # Promote anyone who manages others but whose designation understates it.
    managed_rm = {h["rm_id"] for h in hierarchy if h["rm_id"]}
    managed_zm = {h["zm_id"] for h in hierarchy if h["zm_id"]}
    for eid in managed_zm:
        if eid in employees:
            employees[eid]["role"] = Role.ZONAL_MANAGER.value
    for eid in managed_rm:
        if eid in employees and employees[eid]["role"] not in (
            Role.ZONAL_MANAGER.value,
        ):
            employees[eid]["role"] = Role.REGIONAL_MANAGER.value

    # Finance and admin staff own no coupons and carry no target, so they
    # appear in no source workbook but the email list. Without this they could
    # sign in only if someone edited employee_master by hand — and until then
    # nobody could upload a sales file at all.
    for ov in role_overrides:
        eid = ov["employee_id"]
        if eid not in email_by_id:
            warn(f"{eid} has a role override but is not in the employee email list — skipped")
            continue
        existing = employees.get(eid, {})
        employees[eid] = {
            **existing,
            "employee_id": eid,
            "full_name": name_by_id.get(eid, eid),
            "initial": existing.get("initial"),
            "email": email_by_id[eid],
            "role": ov["role"],
            "designation": ov.get("designation") or existing.get("designation"),
            "region": existing.get("region"),
            "zone": existing.get("zone"),
            "vertical": "Marrow",
            "is_active": True,
            "effective_from": today,
            "effective_to": None,
            "updated_at": now,
            "updated_by": "bootstrap",
        }
        if eid not in {h["employee_id"] for h in hierarchy}:
            hierarchy.append({
                "employee_id": eid,
                "submanager_id": None, "rm_id": None, "zm_id": None,
                "business_head_id": head_id,
                "region": None, "zone": None,
                "effective_from": today, "effective_to": None,
                "updated_at": now, "updated_by": "bootstrap",
            })

    # Business heads, from the seed rather than from any sales file.
    for h in heads["business_heads"]:
        employees[h["employee_id"]] = {
            **h, "initial": None, "designation": "BH",
            "region": None, "zone": None,
            "effective_from": today, "effective_to": None,
            "updated_at": now, "updated_by": "bootstrap",
        }

    # --- targets ----------------------------------------------------------
    targets = []
    for _, r in targets_df.iterrows():
        base = float(r["base"]) if pd.notna(r["base"]) else 0.0
        targets.append({
            "employee_id": r["employee_id"],
            "period": period,
            "vertical": "Marrow",
            "target_units": base,
            "winner_units": float(r["wc"]) if pd.notna(r["wc"]) else base * 1.3,
            "status": "APPROVED",
            "submitted_by": "bootstrap",
            "approved_by": "bootstrap",
            "approved_at": now,
            "effective_from": today,
            "version": 1,
            "updated_at": now,
        })

    # --- coupon master ----------------------------------------------------
    coupon_rows = []
    for _, r in coupons.iterrows():
        size = str(r["Group Size"]).strip().upper()
        required = int(r["Required Sales"]) if pd.notna(r["Required Sales"]) else \
            REQUIRED_SALES_BY_GROUP_SIZE.get(size, 10)
        coupon_rows.append({
            "coupon_signature": r["coupon_signature"],
            "coupon_code": r["Final coupons"],
            "employee_id": r["Emp ID"],
            "initial": r["Code"],
            "region": r["Zone"],
            "college_id": str(r["College ID"]) if pd.notna(r["College ID"]) else None,
            "discount_group": r["Discount Group"],
            "group_size": size,
            "required_sales": required,
            "min_sales": int(r["Min Sales"]) if pd.notna(r["Min Sales"])
            else min_sales_for(size, required),
            "activation_date": pd.to_datetime(r["Activation Date"]).date().isoformat()
            if pd.notna(r["Activation Date"]) else None,
            "deactivation_date": pd.to_datetime(r["Deactivation Date"]).date().isoformat()
            if pd.notna(r["Deactivation Date"]) else None,
            "upload_batch_id": "BOOTSTRAP",
            "ingested_at": now,
        })
        if size not in MIN_SALES_BY_GROUP_SIZE:
            warnings.append(f"Coupon {r['Final coupons']} has group size {size}, which is not in the Slab table")

    unknown_agents = sorted(set(coupons["Emp ID"]) - set(employees))
    if unknown_agents:
        warnings.append(
            f"{len(unknown_agents)} coupon owners are not in the {period} target sheet "
            f"(e.g. {', '.join(unknown_agents[:5])}) — their sales will calculate "
            f"against a zero target. Most are Inside Sales."
        )

    return {
        "employee_master": list(employees.values()),
        "reporting_hierarchy": hierarchy,
        "targets": targets,
        "coupon_master": coupon_rows,
        "_warnings": warnings,
    }


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="directory holding the source workbooks")
    ap.add_argument("--period", default="2026-08")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be loaded; touches nothing")
    a = ap.parse_args()

    global FILES
    FILES = require_sources(
        Path(a.source), "email_list", "agent_details", "incentive_report",
        "coupon_working",
    )
    print("Source files:")
    for key, path in FILES.items():
        print(f"  {key:22s} {path.name}")
    print()

    data = build(Path(a.source), a.period)
    warnings = data.pop("_warnings")

    print(f"Bootstrap for {a.period}\n")
    for table, rows in data.items():
        print(f"  {table:22s} {len(rows):6,} rows")

    emp = data["employee_master"]
    print(f"\n  roles: ", end="")
    counts: dict[str, int] = {}
    for e in emp:
        counts[e["role"]] = counts.get(e["role"], 0) + 1
    print(", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"  employees without an email: "
          f"{sum(1 for e in emp if not e.get('email'))}")

    if warnings:
        print(f"\n  {len(warnings)} warnings:")
        for w in warnings[:15]:
            print(f"    - {w}")

    if a.dry_run:
        print("\nDry run — nothing written.")
        return 0

    from app.config import get_settings
    from app.db import bigquery as bq

    s = get_settings()

    # The tables have to exist before we can clear them. Checking first turns a
    # BigQuery stack trace into a sentence saying which command to run.
    try:
        existing = {
            r["table_name"]
            for r in bq.query(
                f"SELECT table_name FROM "
                f"`{s.gcp_project_id}.{s.bq_dataset}.INFORMATION_SCHEMA.TABLES`"
            )
        }
    except Exception:
        existing = set()

    needed = {"employee_master", "reporting_hierarchy", "coupon_master", "targets"}
    missing = needed - existing
    if missing:
        print(f"\nThe dataset {s.gcp_project_id}.{s.bq_dataset} is not set up yet.")
        print(f"Missing: {', '.join(sorted(missing))}\n")
        print("Create it first:")
        print("    python -m app.db.ddl --apply --seed-rules\n")
        print("Then run this again. Check what is there with:")
        print(f"    bq ls {s.gcp_project_id}:{s.bq_dataset}")
        return 1

    print(f"\nLoading into {s.gcp_project_id}.{s.bq_dataset} …")

    # Employee master and hierarchy are current-state: replace them.
    for table in ("employee_master", "reporting_hierarchy"):
        bq.query(f"DELETE FROM {s.table(table)} WHERE TRUE")

    # Targets are period-scoped: replace this period only.
    bq.query(f"DELETE FROM {s.table('targets')} WHERE period = @p", {"p": a.period})

    # Coupon signatures ACCUMULATE across months. Each month's workbook carries
    # only its own coupons, so wiping the table would delete every previous
    # month's signatures — and with them the ability to recalculate those
    # months, since qualification depends on the coupon master. Replace only
    # the signatures present in this load, keyed on coupon_signature.
    signatures = [r["coupon_signature"] for r in data["coupon_master"]]
    if signatures:
        for i in range(0, len(signatures), 5000):
            bq.query(
                f"DELETE FROM {s.table('coupon_master')} "
                "WHERE coupon_signature IN UNNEST(@sigs)",
                {"sigs": signatures[i:i + 5000]},
            )
        kept = bq.query(
            f"SELECT COUNT(*) AS n FROM {s.table('coupon_master')}"
        )[0]["n"]
        if kept:
            print(f"  {kept:,} coupon signatures from other periods left in place")

    for table, rows in data.items():
        n = bq.load_rows(table, rows)
        print(f"  {table:22s} {n:6,} loaded")

    from app.db.ddl import seed_rules
    seed_rules()
    print("\nDone. Next: upload a sales file, then recalculate the period.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
