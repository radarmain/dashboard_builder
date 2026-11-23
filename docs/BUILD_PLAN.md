# Build Plan

Work one phase at a time. After each phase, report what works and how to run it, then wait for the go-ahead unless told otherwise.

**Phase 0: Scaffolding.** (DONE) Repo layout, backend FastAPI skeleton with a health route, frontend apps via Vite, .env.example, README with run commands, ruff config. Confirm AWS profile, region, and CDK bootstrap status (read-only checks).

**Phase 1: Core infra (CDK).** (DONE) rdb_main DynamoDB table, S3 data bucket (versioning, cache lifecycle), Cognito user pool with groups and an app client, IAM roles for the API (including read access to GBQ_lambda_secrets). Show the diff first, then deploy. Create one test admin user.

**Phase 2: Backend core, run locally.** (DONE) Cognito JWT verification and group checks. Client / project / view CRUD backed by DynamoDB and S3. Revisions: save draft, publish, list last 10, restore, prune.

**Phase 3: BigQuery connection.** (DONE) Load service account from Secrets Manager. List BQ projects and tables the account can see. Project settings endpoint for selected project and multi-select tables. Schema snapshots (via the BigQuery tables API, not INFORMATION_SCHEMA queries) with editable column descriptions. SQL validation module (sqlglot, allowlist, dry run, byte cap). The service account is global, so the allowlist is the security boundary: write REAL tests that reject other tables, other datasets, subqueries or CTEs referencing disallowed tables, wildcard tables, multi-statement SQL, and DML/DDL. Query execution endpoint with S3 cache.

**Phase 4: Builder UI + shared SpecRenderer.** (DONE) Login (incl. NEW_PASSWORD_REQUIRED), clients list and add, client portal, projects list and create, project settings (BigQuery project + multi-select tables + column descriptions + byte cap), views list and add, and the view editor: shared SpecRenderer (bar, line, area, pie, scatter, table, kpi) on a 12-column grid, manual element editing (SQL, run, chart config, apply), save, publish, revisions drawer, unsaved-changes warning. AI prompt box was a disabled placeholder until Phase 5.

**Phase 5: Chat to chart.** (DONE) Streaming chat endpoint using Bedrock with schema context and structured output matching VIEW_SPEC.md; the chat prompt box in the view editor creates and edits elements by producing specs and SQL that go through the same validation as manual edits.

**Phase 6: Viewer + Client Preview.** (DONE) `/api/portal/*` routes (published only, SQL and internals stripped), `frontend/core` + `frontend/portal` packages, the Viewer app (port 5174), and a "Client Preview" mode in the Builder (`/preview/c/...`, same portal components, own session). View descriptions + `published_summary` for cards. Test client users: `scripts/create_client_user.py`.

**Phase 6.5: User management.** (DONE; backend + UI + tests, deploy after the CDK diff is approved) One Cognito group per user (admin | analyst | client), users are disabled never deleted (the API role has no delete action). Per-client user endpoints (admin or analyst) and admin-only `/api/admin/users*`; access items now have a `CLIENT#/USER#` mirror written atomically (`scripts/backfill_client_user_mirrors.py` for existing grants). Staff emails limited to `RDB_STAFF_EMAIL_DOMAINS`. Builder: client Settings tab (Users) and admin-only `/settings` page.

**Phase 7: Deploy.** (DONE, deployed to default CloudFront domains) Lambda container image (Web Adapter, streaming Function URL) + two S3/CloudFront SPAs with same-origin `/api`, stack `rdb-app`, deploy via `scripts/deploy.sh`, GitHub Actions (`.github/workflows/deploy.yml`, OIDC role `rdb-github-deploy`, main branch only). Default AWS domains only. See README "Deploy".

**Phase 8: Domains.** (DONE; waiting on the Cloudflare records in `docs/DNS_RECORDS.md`, then Step 6 verification.) DNS is on Cloudflare (we only print records). ACM wildcard `*.radar-analytics.com` via `scripts/request_cert.py` (ARN in `infra/config.json`), CloudFront aliases `builder.radar-analytics.com` and `*.radar-analytics.com`, security-headers policy, client-slug rules (`backend/app/slugs.py` + `reserved_slugs.json`), host-based portal routing (`frontend/portal/src/routes.ts`), Copy client link in the Builder.

**Phase 5.5: Tableau token-expiry alert + read-only inventory.** (DONE, deployed; secret `rdb_tableau` created, deployed status call confirmed) `backend/app/tableau_{config,client}.py` (plain REST + Metadata GraphQL, read-only guard, single-session lock), admin-only `GET /api/alerts` + non-dismissible Builder banner (token expiry <=30d warning, <=7d critical; runtime auth failure marker), `GET /api/tableau/{status,projects,workbooks,workbooks/{id}}` (builder only, 5-min cache), `scripts/tableau_inventory.py`, `scripts/create_tableau_secret.py`. No Tableau-to-dashboard import yet.

**Out of scope until asked:** automated Tableau import, CPA integration, review workflows, drag-and-drop layout, extensive testing, visual testing.
