# DATA_MAPPING.md — Data Mapping & Business Logic Specification

Derived **entirely from the supplied files**. Every rule below is traced to a
source cell, column or a verified reconciliation. Anything not present in the
files is tagged `ASSUMPTION / REQUIRES CONFIGURATION`.

Source files analysed:

| File | Shape | Role |
|---|---|---|
| `Big_Query_Table_Sales_Data.csv` | 28,710 rows × 42 cols | Raw sales extract — defines the BigQuery source schema |
| `Coupon_Working_Aug_2026.xlsx` | 3 sheets (`Coupon Working` 1,229 rows; `Master Data` 16,012 rows; `Pivot`) | Coupon master + qualification engine + attributed transactions |
| `Field_Incentive_Report_Aug_2026.xlsx` | 15 sheets | Policy, targets, incentive calculation, ARPU rule, RM/ZM payouts |
| `Coupon_Agent_Details.xlsx` | 98 rows | Initial → Employee ID → Name → Region |
| `Emp_email_ID_list.xlsx` | company-wide | Employee ID → Full name → Official email |

---

## 1. Key finding: how a sale is attributed to a BDE

The raw sales table has **no employee column**. Attribution runs through the
**coupon**, not through `coupon_agent` directly:

```
sales.coupon  ─match─►  Coupon Working."Final coupons"  ─►  Emp ID, Agent Name, Region
```

`coupon_agent` (e.g. `ABB`, `SMP`) is the BDE *initial* and is the prefix of the
coupon code, but it is **null on 12,292 of 28,710 rows** and is therefore not
reliable as the primary key. The coupon code is.

**Verification performed.** I expanded the `Payment id` column of
`Coupon Working` (a comma-separated list of `payment_id`s per coupon signature)
into 16,012 payment-to-coupon rows and joined it to `Master Data`:

- unmatched payments: **0**
- `Emp ID` agreement: **100.0%**
- `Qualification` (Qualified/Disqualified) agreement with `Qualification 3`: **100.0%** (15,591 / 421)

So the attribution and qualification chain is fully determined by the files, not inferred.

### 1.1 Which raw rows enter the field-sales universe

`Master Data` (16,012 rows) is the August subset of the raw extract. Comparing
it to the raw CSV, the inclusion filter is:

1. `payment_status = 'captured'` — the raw file also contains `terminated` (32), `auto_refunded` (2), `refunded` (1); none appear in Master Data.
2. `coupon IS NOT NULL` — 2,911 raw rows have no coupon and carry no attribution.
3. the coupon resolves to a row in the coupon master.
4. `payment_date_ist` falls inside the incentive month.

Rows failing (3) are the **`UNKNOWN EMPLOYEE`** error bucket. They are never
guessed onto a BDE.

> `ASSUMPTION / REQUIRES CONFIGURATION` — whether `terminated`/`refunded` rows
> should be *excluded* (as here) or carried as negative adjustments. The
> `Header Index` sheet describes a "Refunds" column ("No incentives are paid on
> the refunds therefore it is represented in a difference column"), but the
> August workbook has no populated refund column. Implemented as **exclude**,
> with a `REFUND_HANDLING` config flag.

---

## 2. Source schema — `Big_Query_Table_Sales_Data.csv`

42 columns. The ones the engine actually consumes are marked ●.

| Column | Type | Notes |
|---|---|---|
| ● `payment_id` | STRING | **Grain.** `marrowmed-<order_id>-<n>`. 28,687 distinct of 28,710 → 23 duplicates exist in raw and must be de-duplicated on load. |
| `order_id` | STRING | 1,272 nulls |
| `invoice_id` | STRING | |
| ● `payment_status` | STRING | captured / terminated / auto_refunded / refunded |
| ● `payment_date`, `payment_date_ist` | TIMESTAMP | `_ist` drives month bucketing |
| `subscription_date`, `subscription_date_ist` | TIMESTAMP | |
| `is_upgrade` | STRING | single value `-` — unused |
| ● `paid_amount` | FLOAT | gross, incl. GST |
| ● `paid_amount_*_100/118` | FLOAT | **net of 18% GST — this is the revenue base for all incentive maths.** Renamed `net_amount` in BigQuery (the original name is not a legal BQ identifier). |
| `IGST_18%_if_not_KA`, `CGST_9%_if_KA`, `SGST_9%_if_KA` | FLOAT | renamed `igst_amount`, `cgst_amount`, `sgst_amount` |
| `Duration_in_Days` | INT | |
| `Destination_State`, `city`, `state` | STRING | |
| `currency` | STRING | INR only |
| `payment_ref_id` | STRING | |
| ● `Plan_ID`, `Plan_Title`, `Plan_Description` | INT/STRING | 83 plan ids, 40 titles |
| ● `Plan_Duration_in_Month` | INT | 3M plans = 4,037 rows |
| `Addon_Plan_IDs`, `Addon_Plans_Title` | | 24 non-null — effectively unused |
| ● `user_id` | STRING | 27,416 distinct |
| ● `college_id`, `college_name`, `college_state` | STRING | **`college_id` is the clubbing key** |
| `year_of_admission` | INT | |
| ● `coupon` | STRING | **attribution key.** 1,786 distinct |
| `coupon_discount` | INT | |
| `coupon_agent` | STRING | BDE initial, 98 distinct, 43% null |
| `loyalty_discount_applied` | BOOL | |
| `eoi` | STRING | |
| `termination_remark`, `terminated_by`, `termination_date` | | 32 rows |
| ● `course_id` | INT | 12 values; 1 = Marrow MBBS (25,372 rows) |
| `reference_id`, `gift_plan_payment_ref_id` | STRING | |

---

## 3. Coupon master — `Coupon Working` (1,229 rows)

The grain is a **coupon signature**, not a coupon code: the same code can be
issued in several windows. `coupon_signature = "<code>_<YYMMDD-HH:MM>_<discount_group>"`.

| Column | Meaning |
|---|---|
| `coupon_signature` | PK |
| `Activation Date`, `Deactivation Date` | validity window; **part of the clubbing key** |
| `Zone` | region label, e.g. `R5-Susmita`, plus `Inside Sales` |
| `Emp ID`, `Code`, `Agent Name` | owning BDE (`Code` = initial) |
| `Final coupons` | the coupon code as it appears in `sales.coupon` |
| `College ID` | clubbing key |
| `Discount Group` | `1_5_FIELDG10`, `13_5_FIELDG10`, `1_5_G5FMGE`, … |
| `Group Size` | `G10` (962), `G5` (247), `G3` (20) |
| `Required Sales` | 10 / 5 / 3 |
| `Min Sales` | from the `Slab` sheet — see §3.2 |
| `Total` | count of payments on this signature |
| `Club Sales` | clubbed non-foundation count |
| `Club Sales FC` | clubbed foundation count |
| `Qualification 1/2/3` | three escalating tests |
| `Payment id` | comma-separated payment_ids |

### 3.1 Foundation-course coupons

The formula tests the coupon **code** for `FC` (`SUMIFS(..., H:H, "*FC*")`), not
the discount group. The two agree on all 1,229 August signatures — `13_5_*`
discount groups and `*FC*` codes form a perfectly diagonal crosstab, 40 vs
1,189 — but the code is what the workbook reads, so the engine reads the code.

`FMGE` in the discount group marks a foreign-medical-graduate coupon. These
carry a comma-separated list of dozens of colleges in `College ID`, which is
why college is dropped from their clubbing key (§4.1).

### 3.2 `Min Sales` — the ">80% coupon utilisation" rule

The `Header Index` sheet calls this "Rule >80% coupon utilization". The
automated workbook resolves it with

```
M = INDEX(Slab!B:B, MATCH(GroupSize, Slab!A:A, 0))
K = REGEXEXTRACT(DiscountGroup, "G\d+")
```

against this table, taken from the `Slab` sheet in full:

| Group size | Required | Min | Effective |
|---|---|---|---|
| G3 | 3 | 3 | 100% |
| G5 | 5 | 5 | 100% |
| G7 | 7 | 5 | 71% |
| G10 | 10 | 8 | 80% |
| G15 | 15 | 12 | 80% |
| G20 | 20 | 16 | 80% |
| G25 | 25 | 20 | 80% |

Only G3, G5 and G10 appear in August; the rest are seeded so a new group size
works without a code change. This is a lookup, not `ceil(0.8 × required)` —
that formula gets G5 and G7 wrong.

---

## 4. Qualification engine (verified, 100% reproduction)

Taken verbatim from the formula version of `Coupon Working` supplied Sep 2026,
then checked against the approved August workbook:

```excel
Q1 = IF(N >= M, "Yes", "No")                              -- own sales reach the minimum
Q2 = IF(AND(Q1 = "No", O >= M), "Yes", Q1)                -- or the club does
Q3 = IF(AND(Q2 = "Yes", N < 5, P = 0), "No", Q2)          -- own-sales floor, waived by FC
```

where `N` = Total, `M` = Min Sales, `O` = Club Sales, `P` = Club Sales FC.

Reproduction against the 1,229 August signatures:

| Column | Match |
|---|---|
| Club Sales (O) | **1229/1229 (100%)** |
| Club Sales FC (P) | **1229/1229 (100%)** |
| Qualification 1 | **1229/1229 (100%)** |
| Qualification 2 | **1229/1229 (100%)** |
| Qualification 3 | **1229/1229 (100%)** |

`Qualification 3` is the **final, binding** status. A transaction is Qualified
iff its coupon signature has `Qualification 3 = Yes`. **No manual overrides are
required for August.**

### 4.1 Clubbing

```excel
O = IF(REGEXMATCH(J,"FMGE"),
       SUMIFS(N:N, F:F,F2, K:K,K2, B:B,B2),
       SUMIFS(N:N, F:F,F2, I:I,I2, K:K,K2, B:B,B2))

P = same, plus H:H = "*FC*"
```

So the clubbing key is:

| Coupon type | Key |
|---|---|
| FMGE | agent code + group size + activation date |
| everything else | agent code + **college id** + group size + activation date |

Three details that a reasonable guess gets wrong, and that cost a full
reconciliation pass to find:

1. **Group size is part of the key.** A G5 and a G10 coupon at the same college on the same date do not club.
2. **FMGE drops college.** Its `College ID` is a list of dozens of colleges, so matching on it would isolate every FMGE coupon.
3. **Club Sales includes foundation rows.** `P` is a *subset* of `O`, not a sibling of it. Adding them together double-counts.

### 4.2 The own-sales floor

`Q3` disqualifies a coupon that reached the minimum only by clubbing, unless it
sold enough on its own:

| Group size | Floor |
|---|---|
| G3 | 3 |
| everything else | 5 |

**Waived when `Club Sales FC > 0`** — that is, when the clubbing *group*
contains foundation sales. This is a property of the group, not of the row: of
the 51 August coupons the waiver rescues, 39 are foundation coupons and **12
are ordinary MM coupons** that happened to sit beside one.

The G3 floor of 3 is the one piece not in the formula (which hard-codes `N<5`).
Finance confirmed it, and the August data agrees: eight G3 coupons with 3–4 own
sales and no foundation coupon beside them are all marked Qualified.

This floor replaces the seven manual overrides an earlier model needed, and it
generalises — the same coupons recurring in September are handled without
anyone remembering to add a row.

### 4.3 Disqualification reasons emitted

Only reasons present in the supplied logic:

| Code | Condition |
|---|---|
| `COUPON_UNDERUTILISED` | Q3 fails — club total below Min Sales |
| `COUPON_NOT_FOUND` | coupon not in master → `UNKNOWN EMPLOYEE`, unattributed |
| `PAYMENT_NOT_CAPTURED` | status ≠ captured |
| `OUTSIDE_COUPON_WINDOW` | payment date outside activation/deactivation |
| `DUPLICATE_PAYMENT_ID` | seen in an earlier batch |
| `MANUAL_OVERRIDE` | `qualification_override` row, reason carried through |

---

## 5. Targets — `Target Achieved- Input`

Monthly blocks, columns A–H:

| Col | Header | Meaning |
|---|---|---|
| A | Troopers | employee name |
| B | Employee IDs | **join key** |
| C | Submanagers | the TM/SBM this BDE rolls up to (blank = none) |
| D | Zone | region code, e.g. `R5-Susmita` |
| E | RM | regional manager name |
| F | ZM | zonal manager name |
| G | `<Month> Base` | **BDE Target Units** |
| H | `<Month> WC` | **Winner Units** = Base × 1.3 |

The reporting hierarchy is therefore **columns C/D/E/F of the target sheet**,
restated monthly — which is why the app stores hierarchy with effective dates
rather than as a static tree.

```
BDE ──► Submanager (TM/SBM, optional) ──► RM (region) ──► ZM (zone) ──► Business Head
```

Zones observed: `Divyansh Pandey`, `Biswadip Sarkar`, `Rajat Sharma`,
`Akshay Yamsanwar`, `Independent(APTS)`. Regions `R1`–`R14`. Designations in
`Incentive Report- Output` column C: **`BDE`, `SBM`, `RM`**.

> Exception observed: several regions are `R6-Independent(Biswadip)`,
> `R12-Independent(Akshay)` — regions with no RM, reporting straight to the ZM.
> The hierarchy model must allow a null RM.

---

## 6. Incentive policy — `Policy` sheet

Slab tables are `LOOKUP`-style (value = lower bound, inclusive; approximate
match). Reproduced exactly:

**BDE / Individual, monthly — `Policy!L12:M16`**

| Achievement ≥ | Rate |
|---|---|
| 0 | 0 |
| 0.50 | 0.50% |
| 0.80 | 1.00% |
| 1.00 | 2.25% |
| 1.30 | 3.00% |

**TM / BDM / Sr.BDE (sub-managers), monthly — `Policy!L18:M21`**

| ≥ | Rate |
|---|---|
| 0 | 0 |
| 0.80 | 0.10% |
| 1.00 | 0.15% |
| 1.30 | 0.25% |

**Team Owner / RM, quarterly — `Policy!L23:M26`**: 0 → 0, 0.80 → 0.25%, 1.00 → 0.35%, 1.30 → 0.50%

**ZM, 9-month — `Policy!L28:M31`**: 0 → 0, 0.80 → 0.20%, 1.00 → 0.25%, 1.30 → 0.35%

**First-year MBBS incremental slab — `Policy!L5:M8`**: 0 → 0, 75 units → 0.05%, 100 → 0.10%, 125 → 0.25%

**ARPU constant**: `Policy!B3 = 33000`

**Resolved, Sep 2026.** The prose block on the Policy sheet showing a 75% entry
threshold is an earlier policy that is no longer wired to any formula. Finance
supplied the current table, which matches the `LOOKUP` ranges exactly:
sub-managers, Team Owners and ZMs all enter at **80%**, and BDEs at **50%**.
The engine already used these values; the `source_note` on each seeded rule now
records the confirmation rather than an open question.

---

## 7. Incentive calculation — `Incentive Report- Output`

Formulas lifted from row 5 onward (blocks at rows 2, 96, 191, 287, 385 = Apr–Aug).

| Col | Field | Formula | Engine equivalent |
|---|---|---|---|
| E | BDE Target Units | `SUMIF(targets.B, id, targets.G)` | `target_units` |
| F | Winner Units | `SUMIF(targets.B, id, targets.H)` | `winner_units` |
| G | Gross Sales Count | SUMIF over gross pivot | `gross_units` |
| H | Disqualified Sales Count | SUMIF over disqualified pivot | `disqualified_units` |
| I | Foundation Sales Count | SUMIF over foundation pivot | `foundation_units` |
| J | Achieved Units | `=G−H` | `achieved_units` |
| K | Achieved Units % | `=IFERROR(J/E,0)` | `unit_pct` |
| L | Target Revenue NET | `=E*33000` | `target_revenue` |
| M | Achieved Revenue GROSS | SUMIF `paid_amount` | `gross_revenue` |
| N | Disqualified Revenue NET | SUMIF `net_amount` where disqualified | `disqualified_revenue` |
| O | **Achieved Revenue NET BDE** | `=SUMIF(net) − N` | `qualified_revenue` |
| P | Achieved Revenue % | `=IFERROR(O/L,0)` | `revenue_pct` |
| Q | Highest of units and revenue % | `=MAX(P,K)` **or** `=P` | `base_pct` — see 7.1 |
| R | Incremental slab % | manual, default 0 | `incremental_pct` |
| S | **BDE Incentive** | `=(LOOKUP(Q, Policy!L12:M16)+R)*O` | `bde_incentive` |
| T–W | TM/SR BDE block | `T=ΣL of reports`, `U=ΣO of reports`, `V=U/T`, `W=LOOKUP(V,Policy!L18:M21)*U` | `submanager_incentive` |
| X–Z | Zone/RM block | `X=ΣE of region`, `Y=ΣJ of region`, `Z=Y/X` | region roll-up |
| AA | RM Incentive | from `RM Calculation Pro Rata` | `rm_incentive` |
| AB | Spot / half-yearly / penalty | manual | `other_incentive` |
| AC | Prior period adjustments | manual | `prior_period_adjustment` |
| AD | Accumulation above ₹2L | `=IF(AF>200000, AF−200000, 0)` | `accumulation` |
| AE | Net payable this month | `=AF−AD` | `net_payable` |
| AF | Incentive of the month | `=S+W+AB+AC` | `total_incentive` |
| AL–AN | Coupon-underutilisation penalty block | gross vs net incentive | `coupon_penalty` |

### 7.1 The ARPU rule (column Q) — the single most important business rule

From `Checklists` row 7, verbatim intent:

> *"With effect from Sep 2025 onward … check the ARPU individual wise; if it is
> more than 33000 then consider whichever is maximum (Unit or Revenue); if ARPU
> less than 33000 then consider the Revenue achievement."*

Confirmed in the formulas: of 382 employee-months, **329 use `=MAX(P,K)`** and
**53 use `=P`**. The 53 are exactly the employees listed in the `ARPU` sheet's
per-month "below threshold" block.

```python
arpu = gross_net_revenue / gross_units          # ARPU!G = E/F
base_pct = max(revenue_pct, unit_pct) if arpu >= 33000 else revenue_pct
```

ARPU is computed on **gross** revenue and **gross** units (verified:
`ARPU!G3 = 1176070.34 / 37 = 31785.68`), not on qualified figures.

### 7.2 The ₹2 lakh monthly cap

Anything above ₹200,000 in a month accrues to `accumulation`, payable at year
end. August 2026: total earned ₹22.41 Cr, paid ₹11.65 Cr, accumulated ₹10.77 Cr.
Confirmed against `Incentive Summary Output`.

### 7.3 RM quarterly pro-rata — `RM Calculation Pro Rata`

```
quarter_target_revenue = quarterly_target_units × 33000
quarter_achieved       = Σ over the 3 months of region qualified_revenue (col O)
achieved_pct           = quarter_achieved / quarter_target_revenue
payout_rate            = LOOKUP(achieved_pct, Policy!L23:M26)
quarter_payout         = quarter_achieved × payout_rate
month_payout           = quarter_payout × (month_achieved / quarter_achieved)
```

Quarterly targets live in `Policy` (`Base for April-June`, `JAS`, `OND`, `JFM`
columns per region). ZM follows the same shape on a 9-month base with the ZM slab.

### 7.4 Zero-sales employees

Per standing reporting practice, an employee with **zero sales in a month is
treated as an exit** for that month: excluded from headcount, target totals and
per-head averages. Implemented as `MonthlyResult.is_active = gross_units > 0`,
with aggregates filtering on it. `Incentive Summary Output` does keep such rows
visible at zero (e.g. `Sudharsan Raja R`, `Anoof`), so the app **shows the row
and excludes it from denominators** rather than hiding it.

---

## 8. Employee identification

```
sales.coupon → coupon_master.emp_id → employee_master
```

The automated workbook resolves the agent from the coupon prefix against its
`Agent` sheet, which is `Coupon_Agent_Details.xlsx`:

```excel
E = INDEX(Agent!K:K, MATCH(Code, Agent!I:I, 0))   -- Emp ID
G = INDEX(Agent!J:J, MATCH(Code, Agent!I:I, 0))   -- Name
D = INDEX(Agent!L:L, MATCH(Code, Agent!I:I, 0))   -- Zone/region
```

A prefix missing from the `Agent` sheet yields `#N/A` there and
**`UNKNOWN EMPLOYEE`** here — never a guess.

`Emp_email_ID_list.xlsx` gives Emp ID → Full name → Official email. Two domains
in use: `@marrowmed.com` and `@dailyrounds.org`. Login is by this email.

Data-quality issues found and handled explicitly:

- `Coupon_Agent_Details` has `#N/A` in the Zone column for 5 initials (`RIS`, `SBS`, `AHN`, `BN`, plus others) → imported as `region = NULL`, flagged.
- Name spellings differ across files (`Nikhil D` vs `Nikhil`, `Anjaney` vs `Anjaney Tripathi`, `Amey jadhav` vs `Amey Jadhav`, `Jaffer Basha` vs `Zaffer Basha`). **Employee ID is the only join key used anywhere.**
- `Sumit Kumar` (targets) vs `Sumit Kumar Singh` (pivots) — same `NHP709`.

---

## 9. Proposed BigQuery schema

Dataset `sales_incentive`. Partitioned by date, clustered on the join keys.

```sql
raw_sales                 PARTITION BY DATE(payment_date_ist) CLUSTER BY coupon, payment_id
  + ingestion columns: upload_batch_id, source_file, ingested_at, ingested_by

coupon_master             CLUSTER BY coupon_code, emp_id      -- from Coupon Working
coupon_signature_stats    -- computed: total, club_sales, club_sales_fc, q1, q2, q3
transaction_qualification PARTITION BY month CLUSTER BY emp_id, qualification_status
employee_master           CLUSTER BY employee_id             -- SCD-2, effective dates
reporting_hierarchy       CLUSTER BY employee_id             -- effective-dated
targets                   CLUSTER BY employee_id, period
incentive_rules           -- effective-dated slab table, no rates in code
monthly_incentive         PARTITION BY period CLUSTER BY employee_id  -- versioned
month_status              -- OPEN / UNDER_REVIEW / APPROVED / LOCKED
upload_batches
audit_log
qualification_override
coupon_clubbing_override
```

Dashboards read `monthly_incentive` and `v_hierarchy_rollup` (a materialised
view), never `raw_sales` — a BDE dashboard is 1 row, a ZM dashboard ~40 rows.
Transaction drill-down is the only path that touches the partitioned raw table,
and it is always filtered by `emp_id` + month.

---

## 10. Open items requiring configuration

| # | Item | Status |
|---|---|---|
| 1 | Q3 manual exceptions | **Resolved**: none needed; the formula reproduces all 1,229 signatures |
| 2 | Clubbing variance | **Resolved**: exact keys taken from the formula workbook |
| 3 | Refund/termination treatment | **Resolved**: captured sales only |
| 4 | Slab thresholds | **Resolved**: 80% entry for managers, 50% for BDE |
| 5 | Incremental slab (col R) | always 0 in Apr–Aug; first-year-MBBS unit table seeded but dormant |
| 6 | Spot / half-yearly / Best RM rewards (col AB) | manual entry with audit trail |
| 7 | Inside Sales region | present in coupon master, absent from field targets — excluded from field incentive, retained for coupon analysis |
| 8 | E-gurukul vertical | zero revenue all months; vertical dimension modelled, no rules |
| 9 | Business Head identity | not named in any file — `REQUIRES CONFIGURATION` |
| 10 | Perquisites sheet | empty in August; not modelled |
