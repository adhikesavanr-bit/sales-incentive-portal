#!/usr/bin/env bash
# Deploy to Cloud Run with least-privilege BigQuery access.
set -euo pipefail

PROJECT="${GCP_PROJECT_ID:?set GCP_PROJECT_ID}"
REGION="${REGION:-asia-south1}"
DATASET="${BQ_DATASET:-sales_incentive}"
SA="incentive-api@${PROJECT}.iam.gserviceaccount.com"

echo "==> Service account"
gcloud iam service-accounts create incentive-api \
  --project "$PROJECT" --display-name "Incentive portal API" || true

# jobUser lets the service run queries. Table-level read/write is granted on the
# dataset only, so the service cannot reach anything else in the project.
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${SA}" --role roles/bigquery.jobUser

bq update --source /dev/stdin "${PROJECT}:${DATASET}" <<JSON
{"access":[{"role":"WRITER","userByEmail":"${SA}"}]}
JSON

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${SA}" --role roles/storage.objectAdmin \
  --condition="expression=resource.name.startsWith('projects/_/buckets/${PROJECT}-incentive-uploads'),title=uploads-only"

echo "==> Secrets"
for key in JWT_SECRET GOOGLE_OAUTH_CLIENT_SECRET; do
  gcloud secrets describe "$key" --project "$PROJECT" >/dev/null 2>&1 || \
    gcloud secrets create "$key" --project "$PROJECT" --replication-policy automatic
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
  --set-env-vars "GCP_PROJECT_ID=${PROJECT},BQ_DATASET=${DATASET},BQ_LOCATION=${REGION},ALLOWED_EMAIL_DOMAINS=${ALLOWED_EMAIL_DOMAINS:-marrowmed.com,dailyrounds.org},GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}" \
  --set-secrets "JWT_SECRET=JWT_SECRET:latest,GOOGLE_OAUTH_CLIENT_SECRET=GOOGLE_OAUTH_CLIENT_SECRET:latest" \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 10

API_URL=$(gcloud run services describe incentive-api \
  --project "$PROJECT" --region "$REGION" --format 'value(status.url)')

echo "==> Frontend"
gcloud run deploy incentive-web \
  --project "$PROJECT" --region "$REGION" \
  --source ./frontend \
  --allow-unauthenticated \
  --set-build-env-vars "NEXT_PUBLIC_API_BASE_URL=${API_URL},NEXT_PUBLIC_GOOGLE_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}" \
  --cpu 1 --memory 512Mi

WEB_URL=$(gcloud run services describe incentive-web \
  --project "$PROJECT" --region "$REGION" --format 'value(status.url)')

# The API only accepts browser requests from the frontend's origin. Without
# this the first page load fails CORS, which looks like a broken login.
echo "==> Allowing the frontend origin on the API"
gcloud run services update incentive-api \
  --project "$PROJECT" --region "$REGION" \
  --update-env-vars "CORS_ORIGINS=${WEB_URL}${CUSTOM_DOMAIN:+,https://$CUSTOM_DOMAIN}"

echo
echo "Done."
echo "  Website : ${WEB_URL}"
echo "  API     : ${API_URL}"
echo
echo "Next:"
echo "  1. Add ${WEB_URL} to the OAuth client's authorised JavaScript origins."
echo "  2. Sign in and confirm your account resolves to a role."
echo "  3. Optional: map a custom domain (see README)."
