#!/usr/bin/env bash
# Collect the details needed to point the app at your existing BigQuery tables.
#
#   bash inspect_bigquery.sh primeval-door-448605-f4 Revenue
#
# Read-only: every statement is SHOW or SELECT. Nothing is created, altered or
# deleted, and no credential is written anywhere. Output goes to
# bigquery-inspection.txt in the current folder — read it before sharing, and
# redact anything you would rather not pass on.

set -uo pipefail

PROJECT="${1:-}"
DATASET="${2:-Revenue}"
OUT="bigquery-inspection.txt"

if [ -z "$PROJECT" ]; then
  echo "Usage: bash inspect_bigquery.sh <project-id> [dataset]"
  exit 1
fi

if ! command -v bq >/dev/null 2>&1; then
  echo "The 'bq' command was not found. Install the Google Cloud CLI:"
  echo "    https://cloud.google.com/sdk/docs/install"
  exit 1
fi

q() { bq query --project_id="$PROJECT" --use_legacy_sql=false --format=pretty --quiet "$1" 2>&1; }

{
  echo "BigQuery inspection"
  echo "project : $PROJECT"
  echo "dataset : $DATASET"
  echo "run at  : $(date)"
  echo

  echo "=== Dataset location and tables ================================="
  bq show --project_id="$PROJECT" --format=prettyjson "$PROJECT:$DATASET" 2>&1 \
    | grep -Ei '"location"|"datasetId"|"description"'
  echo
  bq ls --project_id="$PROJECT" --max_results=200 "$PROJECT:$DATASET" 2>&1
  echo

  for TABLE in $(bq ls --project_id="$PROJECT" --max_results=200 --format=sparse "$PROJECT:$DATASET" 2>/dev/null \
                 | tail -n +3 | awk '{print $1}'); do
    echo "=== $DATASET.$TABLE ============================================"

    echo "--- columns ---"
    q "SELECT column_name, data_type, is_nullable
       FROM \`$PROJECT.$DATASET.INFORMATION_SCHEMA.COLUMNS\`
       WHERE table_name = '$TABLE'
       ORDER BY ordinal_position"

    echo "--- size, partitioning and clustering ---"
    q "SELECT table_name, ddl
       FROM \`$PROJECT.$DATASET.INFORMATION_SCHEMA.TABLES\`
       WHERE table_name = '$TABLE'"

    echo "--- row count ---"
    q "SELECT COUNT(*) AS rows FROM \`$PROJECT.$DATASET.$TABLE\`"

    echo
  done

  echo "=== Does a date and coupon column exist in each table? =========="
  q "SELECT table_name, column_name, data_type
     FROM \`$PROJECT.$DATASET.INFORMATION_SCHEMA.COLUMNS\`
     WHERE LOWER(column_name) LIKE '%date%'
        OR LOWER(column_name) LIKE '%coupon%'
        OR LOWER(column_name) LIKE '%payment%'
        OR LOWER(column_name) LIKE '%amount%'
     ORDER BY table_name, ordinal_position"

  echo
  echo "=== Your permissions on this project ============================"
  gcloud projects get-iam-policy "$PROJECT" \
    --flatten="bindings[].members" \
    --filter="bindings.members:$(gcloud config get-value account 2>/dev/null)" \
    --format="value(bindings.role)" 2>&1

} > "$OUT" 2>&1

echo "Written to $OUT ($(wc -l < "$OUT") lines)."
echo
echo "It contains column names, types, row counts and table DDL — no row data,"
echo "so no customer or employee records. Have a look, then share it."
