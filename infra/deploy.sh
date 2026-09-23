#!/usr/bin/env bash
# Deploy to Cloud Run with least-privilege BigQuery access.
set -euo pipefail

PROJECT="${GCP_PROJECT_ID:?set GCP_PROJECT_ID}"
REGION="${REGION:-asia-south1}"
DATASET="${BQ_DATASET:-sales_incentive}"
# The dataset holding the monthly sales tables, read-only.
SOURCE_DATASET="${SOURCE_DATASET:-}"
SA="incentive-api@${PROJECT}.iam.gserviceaccount.com"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com bigquery.googleapis.com \
  --project "$PROJECT"

echo "==> Service account"
gcloud iam service-accounts create incentive-api \
  --project "$PROJECT" --display-name "Incentive portal API" || true

# IAM is eventually consistent: a service account is not immediately visible to
# the policy API that is about to reference it, and the binding fails with
# "does not exist". Wait for it rather than racing.
echo "  waiting for the service account to propagate"
for attempt in $(seq 1 30); do
  if gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
    echo "  ready"
    break
  fi
  sleep 2
done

# Even once it is describable, the policy API can lag a moment longer, so the
# binding itself is retried.
bind_role() {
  local role="$1" attempt
  for attempt in $(seq 1 10); do
    if gcloud projects add-iam-policy-binding "$PROJECT" \
         --member "serviceAccount:${SA}" --role "$role" >/dev/null 2>&1; then
      echo "  bound ${role}"
      return 0
    fi
    sleep 3
  done
  echo "  FAILED to bind ${role} after several attempts"
  return 1
}

# jobUser lets the service run queries. Dataset access is granted per dataset,
# so the service can write to its own and read the sales tables, and reach
# nothing else in the project.
bind_role roles/bigquery.jobUser

# Append to each dataset's access list rather than replacing it. `bq update
# --source` overwrites the whole list, which would strip every existing grant
# — including your own — from a dataset that other people rely on.
grant_dataset() {
  local dataset="$1" role="$2"
  echo "  granting ${role} on ${dataset}"
  bq show --format=prettyjson "${PROJECT}:${dataset}" > /tmp/ds.json
  python3 - "$role" "$SA" <<'PY'
import json, sys
role, member = sys.argv[1], sys.argv[2]
d = json.load(open("/tmp/ds.json"))
access = d.get("access", [])
if not any(a.get("userByEmail") == member and a.get("role") == role for a in access):
    access.append({"role": role, "userByEmail": member})
    d["access"] = access
    json.dump(d, open("/tmp/ds.json", "w"))
    print("  added")
else:
    print("  already present")
PY
  bq update --source /tmp/ds.json "${PROJECT}:${dataset}"
  rm -f /tmp/ds.json
}

grant_dataset "$DATASET" WRITER
# The service also has to READ the monthly sales tables. Without this the app
# deploys, signs people in, and then fails on every calculation.
if [ -n "${SOURCE_DATASET:-}" ] && [ "${SOURCE_DATASET}" != "${DATASET}" ]; then
  grant_dataset "$SOURCE_DATASET" READER
fi

# Upload staging bucket, only if one is configured. The conditional binding is
# scoped to that single bucket.
if [ -n "${GCS_UPLOAD_BUCKET:-}" ]; then
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${SA}" --role roles/storage.objectAdmin \
    --condition="expression=resource.name.startsWith('projects/_/buckets/${GCS_UPLOAD_BUCKET}'),title=uploads-only" \
    >/dev/null || echo "  could not bind the uploads bucket role; file staging will be unavailable"
fi

echo "==> Secrets"
for key in JWT_SECRET GOOGLE_OAUTH_CLIENT_SECRET; do
  gcloud secrets describe "$key" --project "$PROJECT" >/dev/null 2>&1 || \
    gcloud secrets create "$key" --project "$PROJECT" --replication-policy automatic

  # Creating a secret does not let anything read it. Without this grant the
  # container builds, the revision is created, and the deploy fails at the last
  # step with "Permission denied on secret".
  gcloud secrets add-iam-policy-binding "$key" \
    --project "$PROJECT" \
    --member "serviceAccount:${SA}" \
    --role roles/secretmanager.secretAccessor >/dev/null

  # Nor does creating a secret store a value. An empty secret deploys right up
  # to the last step and then fails with "versions/latest was not found".
  if gcloud secrets versions describe latest --secret "$key" \
       --project "$PROJECT" >/dev/null 2>&1; then
    echo "  ${key}: already has a value"
    continue
  fi

  if [ "$key" = "JWT_SECRET" ]; then
    # Generated here so it never appears on screen or in shell history.
    openssl rand -hex 32 | gcloud secrets versions add "$key" \
      --project "$PROJECT" --data-file=- >/dev/null
    echo "  ${key}: generated"
  else
    echo
    echo "  Paste the OAuth client secret from the Cloud console, then press"
    echo "  Enter followed by Ctrl-D. Nothing will be echoed."
    gcloud secrets versions add "$key" --project "$PROJECT" --data-file=- >/dev/null
    echo "  ${key}: stored"
  fi
done

# The browser calls the API directly, so the service must be reachable from the
# internet. Reachable is not the same as open: every route except /health and
# /ready requires a Google Workspace sign-in whose address exists in
# employee_master, and the caller's scope is derived from that identity.
#
# If you would rather the API were not on the public internet at all, deploy it
# with --no-allow-unauthenticated and put both services behind Identity-Aware
# Proxy. See the "Locking it down further" section of README.md — it needs a
# load balancer, so it is a deliberate step rather than the default.
echo "==> Backend"
gcloud run deploy incentive-api \
  --project "$PROJECT" --region "$REGION" \
  --source ./backend \
  --service-account "$SA" \
  --allow-unauthenticated \
  --set-env-vars "^|^GCP_PROJECT_ID=${PROJECT}|BQ_DATASET=${DATASET}|BQ_LOCATION=${BQ_LOCATION:-US}|SOURCE_PROJECT=${SOURCE_PROJECT:-$PROJECT}|SOURCE_DATASET=${SOURCE_DATASET}|SOURCE_TABLE_TEMPLATE=${SOURCE_TABLE_TEMPLATE:-{MMM\}_{YYYY\}}|NON_FIELD_COUPON_REGIONS=${NON_FIELD_COUPON_REGIONS:-Inside Sales}|ALLOWED_EMAIL_DOMAINS=${ALLOWED_EMAIL_DOMAINS:-marrowmed.com,dailyrounds.org}|GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}" \
  --set-secrets "JWT_SECRET=JWT_SECRET:latest,GOOGLE_OAUTH_CLIENT_SECRET=GOOGLE_OAUTH_CLIENT_SECRET:latest" \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 10

API_URL=$(gcloud run services describe incentive-api \
  --project "$PROJECT" --region "$REGION" --format 'value(status.url)')

echo "==> Frontend"
# API_BASE_URL is a RUNTIME variable, not a build arg: the Next.js server reads
# it when it starts and proxies /api/* to the backend. Nothing about the API
# location or the OAuth client is compiled into the browser bundle.
gcloud run deploy incentive-web \
  --project "$PROJECT" --region "$REGION" \
  --source ./frontend \
  --allow-unauthenticated \
  --set-env-vars "API_BASE_URL=${API_URL}" \
  --cpu 1 --memory 512Mi

WEB_URL=$(gcloud run services describe incentive-web \
  --project "$PROJECT" --region "$REGION" --format 'value(status.url)')

# The browser never calls the API cross-origin — the frontend proxies it — so
# CORS is only needed for local development against a deployed API.
echo "==> Allowing the frontend origin on the API"
gcloud run services update incentive-api \
  --project "$PROJECT" --region "$REGION" \
  --update-env-vars "CORS_ORIGINS=${WEB_URL}${CUSTOM_DOMAIN:+,https://$CUSTOM_DOMAIN}" \
  >/dev/null

echo
echo "Done."
echo "  Website : ${WEB_URL}"
echo "  API     : ${API_URL}"
echo
echo "Next:"
echo "  1. Add ${WEB_URL} to the OAuth client's authorised JavaScript origins."
echo "     BQ_LOCATION is ${BQ_LOCATION:-US}; it must match your datasets."
echo "  2. Sign in and confirm your account resolves to a role."
echo "  3. Optional: map a custom domain (see README)."
