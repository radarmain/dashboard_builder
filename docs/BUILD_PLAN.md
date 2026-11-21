# Build Plan

Work one phase at a time. After each phase, report what works and how to run it, then wait for the go-ahead unless told otherwise.

**Phase 0: Scaffolding.** (DONE) Repo layout, backend FastAPI skeleton with a health route, frontend apps via Vite, .env.example, README with run commands, ruff config. Confirm AWS profile, region, and CDK bootstrap status (read-only checks).

**Phase 1: Core infra (CDK).** (DONE) rdb_main DynamoDB table, S3 data bucket (versioning, cache lifecycle), Cognito user pool with groups and an app client, IAM roles for the API (including read access to GBQ_lambda_secrets). Show the diff first, then deploy. Create one test admin user.

**Phase 2: Backend core, run locally.** (DONE) Cognito JWT verification and group checks. Client / project / view CRUD backed by DynamoDB and S3. Revisions: save draft, publish, list last 10, restore, prune.

**Phase 3: BigQuery connection.** (DONE) Load service account from Secrets Manager. List BQ projects and tables the account can see. Project settings endpoint for selected project and multi-select tables. Schema snapshots (via the BigQuery tables API, not INFORMATION_SCHEMA queries) with editable column descriptions. SQL validation module (sqlglot, allowlist, dry run, byte cap). The service account is global, so the allowlist is the security boundary: write REAL tests that reject other tables, other datasets, subqueries or CTEs referencing disallowed tables, wildcard tables, multi-statement SQL, and DML/DDL. Query execution endpoint with S3 cache.

**Phase 4: Builder UI + shared SpecRenderer.** (DONE) Login (incl. NEW_PASSWORD_REQUIRED), clients list and add, client portal, projects list and create, project settings (BigQuery project + multi-select tables + column descriptions + byte cap), views list and add, and the view editor: shared SpecRenderer (bar, line, area, pie, scatter, table, kpi) on a 12-column grid, manual element editing (SQL, run, chart config, apply), save, publish, revisions drawer, unsaved-changes warning. AI prompt box is present but disabled.

**Phase 5: Chat to chart.** Streaming chat endpoint using Bedrock with schema context and structured output matching VIEW_SPEC.md; the chat prompt box in the view editor creates and edits elements by producing specs and SQL that go through the same validation as manual edits.

**Phase 6: Viewer.** Viewer app that loads a published view by client, project, and view slug, with client access control. Run it locally first.

**Phase 7: Deploy.** Fargate service (smallest size) behind an ALB or equivalent, CloudFront for the static apps, default AWS domains. Smoke test with basic API calls. Then add GitHub Actions: pushes to main trigger deploys by calling `scripts/deploy.sh`, authenticating via GitHub OIDC (no stored keys). First check whether an IAM OIDC provider for `token.actions.githubusercontent.com` already exists in the account; reuse it without modifying it, otherwise create it. The deploy role is an `rdb-` prefixed role scoped to rdb stacks. Do not set up Actions before this phase.

**Phase 8: Domains (only when asked).** Route 53, ACM wildcard cert, CloudFront Function for client and project routing, builder.radar-analytics.com and *.radar-analytics.com.

**Out of scope until asked:** automated Tableau import, CPA integration, review workflows, drag-and-drop layout, extensive testing, visual testing.
