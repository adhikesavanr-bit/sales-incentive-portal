-- BigQuery schema for the Sales & Incentive Management Portal.
-- Run with: bq query --use_legacy_sql=false < schema.sql
-- ${PROJECT} and ${DATASET} are substituted by app/db/ddl.py.

CREATE SCHEMA IF NOT EXISTS `${PROJECT}.${DATASET}`
  OPTIONS (location = '${LOCATION}');

-- ---------------------------------------------------------------------------
-- Raw sales. Mirrors Big_Query_Table_Sales_Data.csv plus ingestion metadata.
-- Column names normalised: `paid_amount_*_100/118` -> net_amount (the original
-- is not a legal BigQuery identifier), GST columns likewise.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.raw_sales` (
  payment_id               STRING  NOT NULL,
  order_id                 STRING,
  invoice_id               STRING,
  payment_status           STRING  NOT NULL,
  payment_date             TIMESTAMP,
  payment_date_ist         TIMESTAMP NOT NULL,
  subscription_date        TIMESTAMP,
  subscription_date_ist    TIMESTAMP,
  is_upgrade               STRING,
  paid_amount              NUMERIC NOT NULL,   -- gross, incl. GST
  net_amount               NUMERIC NOT NULL,   -- paid_amount * 100/118
  igst_amount              NUMERIC,
  cgst_amount              NUMERIC,
  sgst_amount              NUMERIC,
  duration_in_days         INT64,
  destination_state        STRING,
  currency                 STRING,
  payment_ref_id           STRING,
  plan_id                  INT64,
  plan_title               STRING,
  plan_description         STRING,
  plan_duration_in_month   INT64,
  addon_plan_ids           STRING,
  addon_plans_title        STRING,
  user_id                  STRING,
  city                     STRING,
  state                    STRING,
  college_id               STRING,
  college_name             STRING,
  college_state            STRING,
  year_of_admission        INT64,
  coupon                   STRING,
  coupon_discount          INT64,
  coupon_agent             STRING,             -- BDE initial, null on ~43% of rows
  loyalty_discount_applied BOOL,
  eoi                      STRING,
  termination_remark       STRING,
  terminated_by            STRING,
  termination_date         TIMESTAMP,
  course_id                INT64,
  reference_id             STRING,
  gift_plan_payment_ref_id STRING,
  -- ingestion metadata
  upload_batch_id          STRING  NOT NULL,
  source_file              STRING,
  ingested_at              TIMESTAMP NOT NULL,
  ingested_by              STRING
)
PARTITION BY DATE(payment_date_ist)
CLUSTER BY coupon, payment_id
OPTIONS (description = 'Raw field-sales transactions as loaded from source extracts.');

-- ---------------------------------------------------------------------------
-- Coupon master. Grain = coupon signature, not coupon code.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.coupon_master` (
  coupon_signature   STRING NOT NULL,
  coupon_code        STRING NOT NULL,   -- joins raw_sales.coupon
  employee_id        STRING NOT NULL,
  initial            STRING,
  region             STRING,
  college_id         STRING,
  discount_group     STRING,            -- 13_5_* => foundation course
  group_size         STRING,            -- G10 / G5 / G3
  required_sales     INT64,
  min_sales          INT64,             -- G10->8, G5->5, G3->3
  activation_date    DATE,
  deactivation_date  DATE,
  upload_batch_id    STRING,
  ingested_at        TIMESTAMP
)
CLUSTER BY coupon_code, employee_id;

-- ---------------------------------------------------------------------------
-- Qualification output, one row per transaction.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.transaction_qualification` (
  period                  STRING NOT NULL,   -- 'YYYY-MM'
  payment_id              STRING NOT NULL,
  employee_id             STRING,
  coupon_signature        STRING,
  status                  STRING NOT NULL,   -- QUALIFIED / DISQUALIFIED / UNATTRIBUTED
  disqualification_reason STRING,
  reason_detail           STRING,
  is_foundation           BOOL,
  coupon_region           STRING,
  is_field                BOOL,              -- false: outside the field scheme
  paid_amount             NUMERIC,
  net_amount              NUMERIC,
  calculation_version     INT64,
  calculated_at           TIMESTAMP
)
-- Not partitioned: `period` is a STRING ('2026-08') and BigQuery only
-- partitions on a real DATE or TIMESTAMP column. Clustering on period first
-- gives block-level pruning for the one query shape this table has —
-- "one period, one employee" — without carrying a second date column that
-- every writer would have to keep in step with `period`.
CLUSTER BY period, employee_id, status;

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.signature_qualification` (
  period            STRING NOT NULL,
  coupon_signature  STRING NOT NULL,
  coupon_code       STRING,
  employee_id       STRING,
  is_foundation     BOOL,
  total             INT64,
  club_sales        INT64,
  club_sales_fc     INT64,
  min_sales         INT64,
  qualification_1   BOOL,
  qualification_2   BOOL,
  qualification_3   BOOL,
  min_own_sales     INT64,             -- own-sales floor applied, 3 for G3 else 5
  own_sales_met     BOOL,              -- false when clubbing was refused by the floor
  overridden        BOOL,
  override_reason   STRING,
  calculated_at     TIMESTAMP
)
CLUSTER BY employee_id, coupon_code;

-- ---------------------------------------------------------------------------
-- Employee master (slowly changing, effective-dated).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.employee_master` (
  employee_id      STRING NOT NULL,
  full_name        STRING NOT NULL,
  initial          STRING,
  email            STRING,
  role             STRING NOT NULL,   -- SUPER_ADMIN..BDE
  designation      STRING,            -- raw workbook value: BDE / SBM / RM
  region           STRING,
  zone             STRING,
  vertical         STRING,
  is_active        BOOL NOT NULL,
  -- The day they left. Kept rather than deleting the row: their past months
  -- still need an owner, and the audit trail must stay readable.
  exit_date        DATE,
  effective_from   DATE NOT NULL,
  effective_to     DATE,
  updated_at       TIMESTAMP,
  updated_by       STRING
)
CLUSTER BY employee_id, email;

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.reporting_hierarchy` (
  employee_id       STRING NOT NULL,
  submanager_id     STRING,   -- TM / SBM / Sr.BDE, may be null
  rm_id             STRING,   -- null for Independent regions
  zm_id             STRING,
  business_head_id  STRING,
  region            STRING,
  zone              STRING,
  effective_from    DATE NOT NULL,
  effective_to      DATE,
  updated_at        TIMESTAMP,
  updated_by        STRING
)
CLUSTER BY employee_id;

-- ---------------------------------------------------------------------------
-- Targets, with approval trail.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.targets` (
  employee_id    STRING NOT NULL,
  period         STRING NOT NULL,
  vertical       STRING,
  target_units   NUMERIC NOT NULL,
  winner_units   NUMERIC,
  status         STRING NOT NULL,   -- DRAFT / SUBMITTED / APPROVED
  submitted_by   STRING,
  approved_by    STRING,
  approved_at    TIMESTAMP,
  effective_from DATE,
  version        INT64,
  updated_at     TIMESTAMP
)
CLUSTER BY employee_id, period;

-- ---------------------------------------------------------------------------
-- Configurable incentive rules. No rate is ever hard-coded in the UI.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.incentive_rules` (
  rule_id           STRING NOT NULL,
  scope             STRING NOT NULL,   -- BDE_MONTHLY / SUBMANAGER_MONTHLY / ...
  vertical          STRING,
  role              STRING,
  team              STRING,
  plan_title        STRING,
  threshold         NUMERIC NOT NULL,  -- inclusive lower bound
  rate              NUMERIC NOT NULL,
  min_qualification NUMERIC,
  max_cap           NUMERIC,
  effective_from    DATE NOT NULL,
  effective_to      DATE,
  source_note       STRING,            -- provenance back to the Policy sheet
  created_by        STRING,
  created_at        TIMESTAMP
)
CLUSTER BY scope, effective_from;

-- Coupon qualification thresholds. Effective-dated, because changing a rule
-- must never alter a month that has already been calculated and paid: a run
-- for 2026-08 reads the row that was in force on 2026-08-01, whatever the
-- current rule says.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.coupon_rules` (
  rule_id        STRING NOT NULL,
  group_size     STRING NOT NULL,   -- G3 / G5 / G7 / G10 / G15 / G20 / G25
  required_sales INT64  NOT NULL,   -- the coupon's nominal size
  min_sales      INT64  NOT NULL,   -- clubbed sales needed to qualify
  min_own_sales  INT64  NOT NULL,   -- sales the coupon must make on its own
  effective_from DATE   NOT NULL,
  effective_to   DATE,
  reason         STRING,
  updated_by     STRING,
  updated_at     TIMESTAMP
)
CLUSTER BY group_size, effective_from;

-- ---------------------------------------------------------------------------
-- Calculated incentives, versioned so a locked month keeps its numbers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.monthly_incentive` (
  period                    STRING NOT NULL,
  employee_id               STRING NOT NULL,
  designation               STRING,
  region                    STRING,
  zone                      STRING,
  target_units              NUMERIC,
  winner_units              NUMERIC,
  gross_units               INT64,
  disqualified_units        INT64,
  foundation_units          INT64,
  achieved_units            INT64,
  unit_pct                  NUMERIC,
  target_revenue            NUMERIC,
  gross_revenue             NUMERIC,
  gross_net_revenue         NUMERIC,
  disqualified_revenue      NUMERIC,
  qualified_revenue         NUMERIC,
  revenue_pct               NUMERIC,
  arpu                      NUMERIC,
  arpu_rule_applied         STRING,
  base_pct                  NUMERIC,
  incremental_pct           NUMERIC,
  bde_rate                  NUMERIC,
  bde_incentive             NUMERIC,
  submanager_target_revenue NUMERIC,
  submanager_achieved_revenue NUMERIC,
  submanager_pct            NUMERIC,
  submanager_rate           NUMERIC,
  submanager_incentive      NUMERIC,
  rm_incentive              NUMERIC,
  other_incentive           NUMERIC,
  prior_period_adjustment   NUMERIC,
  coupon_penalty            NUMERIC,
  total_incentive           NUMERIC,
  accumulation              NUMERIC,
  net_payable               NUMERIC,
  is_active                 BOOL,
  slab_hits                 JSON,
  notes                     ARRAY<STRING>,
  calculation_version       INT64 NOT NULL,
  calculated_at             TIMESTAMP,
  calculated_by             STRING
)
-- See the note on transaction_qualification: clustered, not partitioned.
-- This table holds roughly 80 rows per month, so partitioning would cost more
-- in metadata than it saves in scanning.
CLUSTER BY period, employee_id, region;

-- ---------------------------------------------------------------------------
-- Month lifecycle, overrides, uploads, audit.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.month_status` (
  period              STRING NOT NULL,
  status              STRING NOT NULL,  -- OPEN / UNDER_REVIEW / APPROVED / LOCKED
  calculation_version INT64,
  changed_by          STRING,
  changed_at          TIMESTAMP,
  reason              STRING
);

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.qualification_override` (
  period           STRING NOT NULL,
  coupon_signature STRING NOT NULL,
  qualified        BOOL NOT NULL,
  reason           STRING NOT NULL,
  approved_by      STRING NOT NULL,
  approved_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.coupon_clubbing_override` (
  period            STRING NOT NULL,
  group_label       STRING NOT NULL,
  coupon_signatures ARRAY<STRING>,
  reason            STRING NOT NULL,
  approved_by       STRING NOT NULL,
  approved_at       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.manual_adjustment` (
  period                  STRING NOT NULL,
  employee_id             STRING NOT NULL,
  incremental_pct         NUMERIC,
  other_incentive         NUMERIC,
  prior_period_adjustment NUMERIC,
  reason                  STRING NOT NULL,
  entered_by              STRING NOT NULL,
  entered_at              TIMESTAMP
);

-- Which table holds each month's sales. Sales arrive as one versioned table
-- per month (Aug_2026_v1, Aug_2026_v2), so the source is data, not a constant.
-- Superseded rows are kept with is_active = FALSE as an audit trail.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.source_table_config` (
  period          STRING NOT NULL,   -- 'YYYY-MM'
  source_project  STRING,
  source_dataset  STRING NOT NULL,
  source_table    STRING NOT NULL,
  date_column     STRING,
  column_map      JSON,              -- canonical -> actual, only when names differ
  is_active       BOOL   NOT NULL,
  updated_by      STRING,
  updated_at      TIMESTAMP
)
CLUSTER BY period;

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.upload_batches` (
  batch_id          STRING NOT NULL,
  filename          STRING,
  gcs_uri           STRING,
  period            STRING,
  uploaded_by       STRING,
  uploaded_at       TIMESTAMP,
  total_rows        INT64,
  valid_rows        INT64,
  invalid_rows      INT64,
  duplicate_rows    INT64,
  unknown_coupons   INT64,
  validation_status STRING,   -- PENDING / VALIDATED / FAILED / IMPORTED
  error_report_uri  STRING
);

CREATE TABLE IF NOT EXISTS `${PROJECT}.${DATASET}.audit_log` (
  audit_id        STRING NOT NULL,
  user_email      STRING NOT NULL,
  action          STRING NOT NULL,
  entity_type     STRING,
  affected_record STRING,
  old_value       JSON,
  new_value       JSON,
  reason          STRING,
  ip_address      STRING,
  occurred_at     TIMESTAMP NOT NULL
)
PARTITION BY DATE(occurred_at)
CLUSTER BY user_email, action;

-- ---------------------------------------------------------------------------
-- Aggregated views. Dashboards read these, never raw_sales.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_employee_hierarchy` AS
SELECT
  e.employee_id, e.full_name, e.email, e.role, e.designation,
  e.region, e.zone, e.vertical, e.is_active, e.exit_date,
  h.submanager_id, h.rm_id, h.zm_id, h.business_head_id
FROM `${PROJECT}.${DATASET}.employee_master` e
LEFT JOIN `${PROJECT}.${DATASET}.reporting_hierarchy` h
  ON e.employee_id = h.employee_id AND h.effective_to IS NULL
WHERE e.effective_to IS NULL;

CREATE OR REPLACE VIEW `${PROJECT}.${DATASET}.v_incentive_current` AS
SELECT m.*
FROM `${PROJECT}.${DATASET}.monthly_incentive` m
JOIN (
  SELECT period, employee_id, MAX(calculation_version) AS v
  FROM `${PROJECT}.${DATASET}.monthly_incentive`
  GROUP BY period, employee_id
) latest
  ON m.period = latest.period
 AND m.employee_id = latest.employee_id
 AND m.calculation_version = latest.v;
