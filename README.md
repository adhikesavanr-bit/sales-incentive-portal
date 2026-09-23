# Sales & Incentive Management Portal

Field-sales incentive calculation for ~100 BDEs, built on BigQuery. The
business logic is reproduced from the approved incentive workbook rather than
reinvented — see [DATA_MAPPING.md](./DATA_MAPPING.md) for the derivation, cell
by cell.

## Verification

The engines are checked against the supplied August 2026 workbook. This is the
acceptance gate for any change:

```bash
cd backend
python -m tests.reconcile_august /path/to/source/files
```

Current result:

```
QUALIFICATION ENGINE
  transactions matched : 16,012 / 16,012 (100.0000%)

INCENTIVE ENGINE
  gross units             79 / 79    max delta 0.00
  qualified revenue       79 / 79    max delta 0.00
  base %                  79 / 79    max delta 0.00
  BDE incentive           79 / 79    max delta 0.00
  total incentive         79 / 79    max delta 0.00

  workbook total incentive : Rs. 22,414,108.79
  engine   total incentive : Rs. 22,414,108.79
```

**No manual overrides are needed.** The qualification rules are taken verbatim
from the formula version of `Coupon Working` and reproduce Club Sales, Club
Sales FC and all three qualification tests at 100% across the 1,229 August
signatures. The override table remains for genuine one-off Finance decisions
and is empty for this period.

## Layout

```
backend/
  app/
    config.py                 settings, all from environment
    engines/
      qualification.py        coupon rules — pure, no IO
      incentive.py            slabs, ARPU rule, cap — pure, no IO
    auth/
      rbac.py                 permissions and scope predicates
      deps.py                 Google sign-in, session tokens
    db/
      schema.sql              BigQuery DDL
      bigquery.py             parameterised query layer
    services/                 upload, employees, targets, dashboards, audit, month
    routers/                  REST API
  scripts/bootstrap.py        loads master data from the source workbooks
  tests/                      144 unit tests + the reconciliation harness
frontend/                     Next.js + TypeScript + Tailwind
infra/deploy.sh               Cloud Run deployment
```

## How a month runs

```
Finance uploads a sales export
        │
        ▼  validate: columns, types, duplicates, unknown coupons
   dry-run qualification  ── nothing written yet
        │
        ▼  admin confirms
   raw_sales (BigQuery, partitioned by payment date)
        │
        ▼  recalculate
   qualification engine ──► transaction_qualification
        │
        ▼
   incentive engine ──► monthly_incentive (versioned)
        │
        ▼
   OPEN → UNDER_REVIEW → APPROVED → LOCKED
```

Recalculating writes a new `calculation_version`; nothing is overwritten, so a
locked month keeps the numbers it was locked with.

## Deploying

Four stages. Stop after stage 1 if you only want to prove the numbers.

### 1. Verify offline — no cloud account needed

Put the five source files in one folder, then from the project root:

```bash
./verify.sh ~/Downloads/workbooks
```

That creates an isolated environment in `backend/.venv`, installs the
dependencies, runs the 144 unit tests, reconciles against the August workbook
and dry-runs the bootstrap. Run `./verify.sh` with no argument to do the unit
tests alone.

Doing it by hand instead (note `python3`, not `python`, on macOS and most Linux):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q --ignore=tests/reconcile_august.py
python -m tests.reconcile_august ~/Downloads/workbooks
python -m scripts.bootstrap --source ~/Downloads/workbooks --dry-run
```

The dry run reports what master data would be loaded and flags anything it
cannot resolve. Expect 78 employees (55 BDE, 6 sub-manager, 12 RM, 4 ZM, 1
business head), 73 targets and 1,229 coupon signatures for August 2026.

### 2. Create the BigQuery dataset

```bash
export GCP_PROJECT_ID=your-project BQ_DATASET=sales_incentive BQ_LOCATION=asia-south1
gcloud auth application-default login

cd backend
cp ../.env.example .env        # fill in project, OAuth client, JWT secret
python -m app.db.ddl --apply --seed-rules
python -m scripts.bootstrap --source /path/to/workbooks --period 2026-08
```

`ddl --apply` creates 15 tables and 2 views. `bootstrap` loads the employee
master, reporting hierarchy, targets and coupon master — the things the app
cannot derive from a sales file. Re-running it replaces those tables, so it is
safe to repeat.

Two roles cannot be read from any workbook, because the people holding them own
no coupons and carry no target. They are seeded instead:

- `app/db/seeds/business_heads.json` — the Business Head
- `app/db/seeds/role_overrides.json` — Finance and admin staff

Add anyone else who needs to upload sales or lock a month to
`role_overrides.json` with their employee id and a reason. They must already be
in the employee email list; bootstrap takes their name and address from there,
so a rename never leaves the seed stale.

### 3. Google sign-in

In the Cloud console, create an **OAuth 2.0 Client ID** of type *Web
application*, with `http://localhost:3000` and your deployed frontend URL as
authorised JavaScript origins. Put the client id in both `backend/.env` and
`frontend/.env.local`; the secret goes in the backend only.

Only `marrowmed.com` and `dailyrounds.org` addresses can sign in, and only if
the address is in `employee_master`. Change that list with
`ALLOWED_EMAIL_DOMAINS`.

### 4. Cloud Run

```bash
export GCP_PROJECT_ID=your-project
export GOOGLE_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com
bash infra/deploy.sh
```

The script creates a service account with `bigquery.jobUser` plus WRITER on the
one dataset and object access to the one upload bucket, stores the JWT secret
and OAuth secret in Secret Manager, deploys both services, and points the API's
CORS allow-list at the frontend's origin. It prints both URLs when it finishes.

Two services, because they have different jobs:

```
    person's browser
          │
          ▼
    incentive-web    Cloud Run, public    Next.js pages
          │
          ▼
    incentive-api    Cloud Run, public    FastAPI, holds the only GCP identity
          │
          ▼
    BigQuery         one dataset, WRITER only
```

"Public" means reachable, not open. Every API route except `/health` and
`/ready` requires a Google Workspace sign-in whose address exists in
`employee_master`, and the caller's scope is derived from that identity rather
than from anything in the request. The browser never holds a GCP credential —
only a session token that expires in eight hours.

### Giving it a real address

Cloud Run URLs look like `https://incentive-web-xxxxx-el.a.run.app`. For
something people will use monthly, map a subdomain:

```bash
gcloud beta run domain-mappings create \
  --service incentive-web --domain incentive.dailyrounds.org \
  --project "$GCP_PROJECT_ID" --region asia-south1
```

That prints DNS records for whoever manages the domain to add. Once they
resolve, Google issues the certificate automatically. Then add the new address
to the OAuth client's authorised origins and to `CORS_ORIGINS` on the API:

```bash
gcloud run services update incentive-api --region asia-south1 \
  --update-env-vars CORS_ORIGINS=https://incentive.dailyrounds.org
```

### Locking it down further

The default keeps the services reachable and enforces identity in the
application. If you want the site off the public internet entirely — only
reachable from the office network or a signed-in Workspace account at the edge
— put both services behind an external load balancer with Identity-Aware Proxy,
and redeploy the API with `--no-allow-unauthenticated`. That is a deliberate
piece of infrastructure work, not a flag, which is why it is not the default.

### What it will cost

For ~100 people opening a dashboard a few times a month, Cloud Run stays near
the free tier: both services scale to zero between uses, and `min-instances 0`
means you pay only while a request is in flight. The real cost is BigQuery, and
the schema is built to keep it small — dashboards read `monthly_incentive`
(about 80 rows a month), never `raw_sales`. Only the transaction drill-down
touches the partitioned table, and always filtered to one employee and one
month. Expect single-digit dollars a month at this size.

## Where the sales come from

Sales arrive as one versioned table per month — `Monthly_Sub.Aug_2026_v1`, and
a `_v2` when a month is re-cut. So the source is a mapping from period to
table, held as data rather than baked into the code. Three layers, first match
wins:

1. A row in `source_table_config` for that period.
2. `SOURCE_DATASET` + `SOURCE_TABLE_TEMPLATE` in the environment — a naming pattern for months nobody has overridden.
3. Neither — the period falls back to files uploaded through the Finance screen.

Connect a month and check it before saving anything:

```bash
python -m scripts.connect_source --period 2026-08 \
    --dataset Monthly_Sub --table Aug_2026_v1 --dry-run
```

That reads the table's columns, matches them to what the engines need, counts
the rows in the period and reports the payment-status split. Drop `--dry-run`
to save it. Column names are matched ignoring case, spaces and underscores, so
`Payment ID` and `payment_id` both resolve; where they genuinely differ, pass
an explicit `column_map`.

### Changing it later

Nothing about this needs a redeploy.

| Change | How |
|---|---|
| A month is re-cut as `_v2` | `connect_source --period 2026-08 --table Aug_2026_v2`, then recalculate |
| Every future month moves dataset | update `SOURCE_DATASET`, or set a row per period |
| One month lives somewhere odd | a `source_table_config` row for that period only |
| Back to file uploads | deactivate the row; the period falls back to `raw_sales` |

`GET /api/sources` returns every decision ever made, with who made it and why.
Re-pointing a **locked** month is refused — the stored calculation would no
longer match the data behind it, so the month has to be reopened first, which
only `SUPER_ADMIN` can do.

## Testing it end to end

Once stage 2 is done, run a month through the app:

1. Sign in as a Finance address. **Sales upload** appears in the sidebar; if it doesn't, the account's role in `employee_master` is not `FINANCE_ADMIN`.
2. Pick **August 2026**, choose the raw sales CSV, press **Check this file**. Expect around 28,700 rows read, ~16,000 attributable to a coupon, and a qualified/disqualified split. Unknown coupons are listed, never guessed onto a BDE.
3. **Import**, then **Recalculate August 2026** with a reason. The result should read 79 employees and **Rs. 22,414,108.79** earned, **Rs. 11,646,549.36** payable, the balance accumulated.
4. Open **My team** — the roll-up is scoped to whoever you signed in as. Drill into a BDE, then into a transaction.
5. Send the month for review, approve it, lock it. Try to recalculate a locked month; it should refuse.

### Checking access control on the live system

This is the one thing worth testing by hand, because a bug here is invisible
until it matters. Sign in as a BDE, take the session token from the browser's
session storage, and try to read someone else's data:

```bash
curl -H "Authorization: Bearer $BDE_TOKEN" \
     "$API_URL/api/employees/NHP002/dashboard?period=2026-08"
# expect 404 — the id is outside the caller's subtree

curl -H "Authorization: Bearer $BDE_TOKEN" "$API_URL/api/rollup/dashboard?period=2026-08"
# expect 403 — a BDE has no VIEW_TEAM permission

curl -H "Authorization: Bearer $BDE_TOKEN" -X POST \
     "$API_URL/api/incentive/calculate?period=2026-08&reason=test"
# expect 403
```

Repeat as an RM against a BDE outside their region. The scope predicate is
built from the token identity, so nothing in the URL can widen it.

## Local development

```bash
cp .env.example backend/.env          # fill in project, OAuth, JWT secret
cp .env.example frontend/.env.local   # keep only the NEXT_PUBLIC_ lines

cd backend
pip install -r requirements.txt
python -m app.db.ddl --apply --seed-rules
python -m scripts.bootstrap --source /path/to/workbooks --period 2026-08
uvicorn app.main:app --reload --port 8000

cd ../frontend
npm install
npm run dev
```

Or `docker compose up`, with a service-account key mounted read-only from
`./secrets` (gitignored).

```bash
cd backend && python -m pytest tests/ -q --ignore=tests/reconcile_august.py
```

## Security

- The browser never receives a service-account key, private key or BigQuery credential. It holds a session token and nothing else.
- On Cloud Run the backend uses an attached service account; no key file exists. Locally it reads one from a mounted path.
- The service has `bigquery.jobUser` plus WRITER on one dataset, and object access to one bucket. Nothing else in the project is reachable.
- Every query is parameterised. No user input is formatted into SQL text.
- Authorisation is a SQL predicate built from the identity in the token. `?employee_id=NHP002` cannot widen it — `is_in_scope()` binds both the caller's id and the requested id, and an out-of-scope id returns 404 rather than 403, so the endpoint does not leak the org chart.
- Role and hierarchy are re-read from BigQuery on every request rather than baked into the token, so revoking access takes effect immediately.

## Business rules worth knowing

| Rule | Where |
|---|---|
| Sales attach to a BDE through the **coupon**, never through `coupon_agent` (43% null) | DATA_MAPPING §1 |
| Coupon qualifies when clubbed sales reach Min Sales: G10 → 8, G5 → 5, G3 → 3 | §3.2 |
| **and** it made at least 5 sales of its own — 3 for G3 — waived when the clubbing group contains foundation sales | §4.2 |
| Clubbing key is agent + college + group size + activation date; FMGE drops college | §4.1 |
| `Club Sales` counts the whole group including foundation rows; `Club Sales FC` is a subset of it, not a sibling | §4.1 |
| ARPU ≥ ₹33,000 → higher of unit% and revenue%; below → revenue% only | §7.1 |
| Above ₹2,00,000 a month accumulates to year end | §7.2 |
| Only captured sales count; refunds and terminations are excluded | §1.1 |
| RM incentive is quarterly and sits **outside** the monthly total | §7.3 |
| Zero sales in a month is treated as an exit for headcount and averages | §7.4 |

## Open items

Six of the original ten are now resolved: clubbing keys, the own-sales floor,
manual overrides, slab thresholds, refund treatment and the Business Head.
What is still open:

1. **Incremental slab (column R)** — the first-year-MBBS unit table is seeded but dormant; it was 0 for every employee Apr–Aug.
2. **Spot / half-yearly / Best RM rewards (column AB)** — manual entry with an audit trail.
