# AGENTS.md

Read this file first, every session. Then read the docs in docs/ as needed.

## What this project is

RADaR Dashboard Builder ("rdb"): an internal tool where analysts (DAs) connect BigQuery tables to a client project, then chat with an AI to build charts, graphs, and tables. Views are saved as JSON specs and served to clients on per-client subdomains. It is a standalone prototype that will later share ideas and components with our CPA product. See docs/PROJECT_OVERVIEW.md.

## Top priority: speed over perfection

This is a PROTOTYPE. Getting a working end-to-end flow in place quickly matters more than polish.

- Do the simplest thing that works. Do not over-engineer, over-abstract, or add features that were not asked for.
- Tests: write only a handful of basic tests for the riskiest logic (SQL validation, spec validation, revision pruning). No exhaustive suites, no coverage targets. Exception: the SQL allowlist is the security boundary (global service account) and gets real tests (see docs/BUILD_PLAN.md Phase 3).
- Do NOT do live Computer Use, browser automation, or visual/screenshot testing unless I explicitly ask. Verify with quick API calls, scripts, or by reading code.
- Do not stop to ask questions you can resolve with a reasonable assumption. Make the assumption, keep going, and record it in docs/DECISIONS.md. Ask only when blocked or when the action is risky (see AWS rules).
- Keep explanations short. Prefer doing over discussing.

## Stack

- Backend: Python 3.12, FastAPI. Runs locally with uvicorn, deploys to Fargate (long or streaming work such as LLM chat) and Lambda (small stateless endpoints) where it makes sense.
- Frontend: React + Vite + Recharts (TypeScript preferred). Two apps in one repo: the Builder (builder.radar-analytics.com) and the Viewer (renders saved views at [client_slug].radar-analytics.com/[project_slug]).
- Auth: AWS Cognito (groups: admin, analyst, client).
- Storage: S3 for specs, revisions, schema snapshots, and cached query results. DynamoDB (single table) for the client/project/view index and user-to-client access.
- LLM: AWS Bedrock.
- Data source: Google BigQuery (read-only), credentials from AWS Secrets Manager secret `GBQ_lambda_secrets` (env `RDB_GBQ_SECRET_NAME`), key `GLOBAL_GBQ_KEY` (env `RDB_GBQ_SECRET_KEY`), which holds the full Google service account JSON. It belongs to existing systems: read-only, never modify, never print or log its values. The service account is global and likely sees many clients' data, so the per-project table allowlist and SQL validation are the security boundary.
- Infra as code: AWS CDK in Python (see docs/AWS_CONVENTIONS.md).

## Repo layout (create as needed, keep it simple)

```
backend/        FastAPI app, services, models
frontend/
  builder/      Builder UI
  viewer/       Viewer UI
  shared/       Shared chart components and spec renderer
infra/          CDK app (Python)
docs/           Project docs
scripts/        Small helper scripts
```

## Hard rules: AWS safety

- Use the AWS CLI / boto3 profile `concrete` (`--profile concrete` / `AWS_PROFILE=concrete`). Never use any other profile.
- Every resource we create is named with the prefix `rdb_` (or `rdb-` where underscores are not allowed, such as S3 buckets, CloudFormation stack names, and some IAM names) and tagged `Project=RDB`.
- NEVER delete, modify, or overwrite any AWS resource that was not created by this project. When in doubt, it is not ours. Read-only commands against other resources are fine for discovery.
- You may create IAM roles for this project, prefixed `rdb-` and scoped as tightly as practical. Prefer roles over IAM users.
- Deploy only via `scripts/deploy.sh` (single deploy path; safe to rerun). Never run `cdk deploy`, `aws s3 sync` to the web buckets, or `aws lambda update-*` by hand.
- Never modify shared account-level resources (existing VPCs, existing IAM roles or policies, existing Route 53 records, existing certificates, etc.) without asking me first. Prefer creating new project-owned resources.
- Before the first deploy, check whether CDK is already bootstrapped in the account and region. If it is not, ask me before bootstrapping.
- Do not assume a region. Check the `concrete` profile's configured region, state it back to me, and use it consistently.
- Do not touch DNS or custom domains until I ask. Prototype on default CloudFront / load balancer domains first.
- Show me the CDK diff or a plain-language list of resources before any deploy that creates IAM roles, networking, or anything with ongoing cost beyond small usage-based charges.
- Keep costs low: serverless and pay-per-use first, smallest Fargate size, no NAT gateways unless unavoidable (ask first), no provisioned capacity.
- Pruning old revisions of views inside our own S3 prefix or DynamoDB items is allowed (it is our own data).

## Hard rules: data and security

- The Viewer and any browser code NEVER send SQL. Browsers send a view ID; the backend runs only the saved SQL for that view.
- All SQL, whether generated by the LLM or edited by an analyst, must pass validation before execution (see docs/ARCHITECTURE.md): SELECT only, only tables connected to that project, BigQuery dry run first, maximum_bytes_billed set.
- Every API route checks Cognito auth and authorization: admin and analyst can use the Builder; client users can only read published views for the client(s) they are assigned to.
- User management: a user is in exactly ONE group (admin | analyst | client), staff emails must match `RDB_STAFF_EMAIL_DOMAINS`, and Cognito users are NEVER deleted (disable instead; no delete permission or endpoint). Analysts only manage client users via the per-client endpoints.
- No secrets in code, logs, git, or frontend bundles.

## Coding conventions

- Python: type hints, Pydantic models for all specs and API payloads, ruff for lint/format. Keep functions small and files focused.
- Frontend: functional components, hooks, TypeScript where cheap. One shared spec renderer used by both Builder preview and Viewer, so what the analyst previews is what the client sees.
- Config via environment variables with a `.env.example` file. Never commit `.env`.
- Local development should work with one command per service (document in the README). Local mode uses the `concrete` profile against real AWS resources (S3, DynamoDB, Cognito, Bedrock, Secrets Manager); do not build elaborate mocks.
- Log decisions and assumptions in docs/DECISIONS.md as short dated bullets.

## Frontend packages (npm workspaces)

- `frontend/shared`: pure chart/spec renderer, NO MUI.
- `frontend/core`: MUI theme, `--rdb-*` vars, Cognito auth/login, authenticated `api.ts`, brand assets.
- `frontend/portal`: all client-facing screens. Used by BOTH `frontend/viewer` and the Builder's `/preview/*` mode, so change them once.
- Every portal link is built by `frontend/portal/src/routes.ts`. No hand-written portal path strings elsewhere.
- `frontend/viewer` must not import builder code. Portal API routes never expose SQL, drafts, revisions, byte counts or cache flags.

## Domains

- Client slugs are subdomains (`<slug>.radar-analytics.com`). Use the shared reserved-slug list (`backend/app/reserved_slugs.json`, validated by `backend/app/slugs.py`); never hard-code another one.
- DNS is on Cloudflare: never touch Cloudflare/DNS yourself. Print the records for the user (see `docs/DNS_RECORDS.md`).

## UI conventions

- Use the theme tokens (`frontend/core/src/theme.ts`, MUI palette or `--rdb-*` variables). No hard-coded colors outside `theme.ts`, the chart palette (`CHART_COLORS`), and the variable fallbacks in `frontend/shared/src/tokens.ts`.
- No MUI inside `frontend/shared`; style it with `--rdb-*` CSS variables. Tailwind is for layout utilities only.
- Teal (secondary) is for accents; never put white text on it (use `onSecondary`).

## Working agreement

- Read docs/BUILD_PLAN.md and work on the current phase only.
- Finish a thin vertical slice before polishing anything.
- After each phase, tell me in a few lines what works, how to run it, and what is next.
- If a doc here conflicts with something I tell you in chat, chat wins; then update the doc.
