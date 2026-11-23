# RADaR Dashboard Builder (rdb)

Prototype: chat-to-dashboard builder on BigQuery data. See `AGENTS.md` and `docs/`.

AWS: profile `concrete`, region `us-east-2`. Local mode talks to real AWS resources.

## One-time setup

```bash
cp .env.example .env                    # fill in after the infra is deployed
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r infra/requirements.txt
(cd frontend && npm install)
(cd infra && npm install)
```

## Run (one command per service)

```bash
# 1) API -> http://localhost:8000/health   (profile concrete, region us-east-2)
cd backend && AWS_PROFILE=concrete AWS_REGION=us-east-2 ../.venv/bin/uvicorn app.main:app --reload --port 8000

# 2) Builder UI -> http://localhost:5173  (Vite proxies /api to localhost:8000, so no CORS)
cd frontend/builder && npm run dev

# 3) Viewer UI (client-facing) -> http://localhost:5174
cd frontend/viewer && npm run dev

# Or all three at once, with [api]/[ui]/[viewer] log prefixes
scripts/start_local.sh
```

First time only: generate the public Cognito settings for the Builder and Viewer (`frontend/{builder,viewer}/.env.local`, gitignored):

```bash
AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/write_env.py
```

Sign in with your Cognito user. A temporary password prompts you to set a new one.

Handy for curl: `TOKEN=$(.venv/bin/python scripts/get_token.py)`; live API check: `scripts/smoke_test.py project.dataset.table`
(creates and deletes temporary Cognito users; see the file header).

AI chat (Phase 5): the prompt box at the bottom of the view editor streams from `POST /api/clients/{c}/projects/{p}/views/{v}/chat` (SSE) using Bedrock
(`RDB_BEDROCK_MODEL_ID`, default `us.anthropic.claude-sonnet-5-5`, us-east-2). Chat history lives in the browser tab only (component state, not persisted).
Chat only changes your local unsaved draft; you still Save/Publish yourself. CLI test:
`AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/chat_test.py CLIENT PROJECT VIEW "message" [--temp-user]`.

Troubleshooting: if `vite build` says `Cannot find module '../lightningcss.darwin-arm64.node'`, run
`cd frontend && npm install --no-save --workspaces=false lightningcss-darwin-arm64@$(node -p "require('./node_modules/lightningcss/package.json').version")`.

## Deploy (AWS, default domains)

```bash
scripts/deploy.sh          # shows `cdk diff`, asks, deploys rdb-core + rdb-app, builds + publishes both frontends
scripts/deploy.sh --yes    # no prompt (what GitHub Actions runs)
```

Stack `rdb-app`: Lambda container API (`rdb-api`, arm64, Web Adapter, streaming Function URL), two private S3 buckets behind CloudFront
(Builder, Viewer) with same-origin `/api`, and the `rdb-github-deploy` role. URLs are printed at the end and are stack outputs
(`BuilderUrl`, `ViewerUrl`). Local dev and the deployed app share the same table/bucket/user pool.
Tableau (read-only): `AWS_PROFILE=concrete .venv/bin/python scripts/tableau_inventory.py` prints an inventory summary of the Tableau Cloud site and writes the full dump to `.local/tableau_inventory/` (gitignored). `scripts/create_tableau_secret.py` copies the `.env` Tableau values into the `rdb_tableau` secret (refuses to overwrite without `--force`).

Post-deploy checks: `curl <url>/health`, `scripts/smoke_test.py project.dataset.table --base-url <builder-url>`,
`scripts/chat_test.py CLIENT PROJECT VIEW "msg" --temp-user --base-url <builder-url>` (prints time to first event).

Rollback: check out the earlier git commit and run `scripts/deploy.sh --yes` (rebuilds that commit's API image and frontends).
API logs: CloudWatch `/aws/lambda/rdb-api` (14 days retention).
GitHub Actions: `.github/workflows/deploy.yml` deploys on push to `main` (OIDC role `rdb-github-deploy`, no stored keys).

## Custom domains (Phase 8)

- Certificate: `AWS_PROFILE=concrete .venv/bin/python scripts/request_cert.py` (idempotent; prints the ACM validation record; writes the ARN to `infra/config.json`). Empty `cert_arn` = default CloudFront domains only.
- `scripts/deploy.sh` and the GitHub workflow read `infra/config.json` (committed; an ARN is not a secret). No new secrets.
- DNS is on Cloudflare and is added by hand: see `docs/DNS_RECORDS.md` (all DNS only / grey cloud).
- Viewer host mode: `https://<client>.radar-analytics.com/:project/:view`. Override the base domain with `VITE_BASE_DOMAIN`. Local dev stays in path mode.

## Viewer and Client Preview

- Viewer (`:5174`): clients sign in and see cards for their client's PUBLISHED views; click one to open it. Admin/analyst can open `/c/<client>` directly.
- Client Preview (Builder): the "Client Preview" button on the client page, project page and view editor opens `/preview/c/...`, which renders the same portal screens with your own session (published data only). "Close Preview" returns to where you came from.
- Make a test client user (emails a temporary password; pool `rdb-users` only):
  `AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/create_client_user.py someone@example.com <client-slug>`
- `cd frontend && npm run setup` makes sure Tailwind's `lightningcss` platform binary is installed (start_local.sh runs it).

## User management (Phase 6.5)

- Client users: Builder > client > **Settings** tab > Users (admin or analyst). Staff users: Builder > **Settings** (admin only).
- One group per user (admin | analyst | client). Users are disabled, never deleted. Staff emails must be on `RDB_STAFF_EMAIL_DOMAINS` (default `radar-analytics.com`).
- Invites are Cognito's default email (about 50/day, may land in spam; SES is a later improvement).
- After deploying this phase once: `AWS_PROFILE=concrete .venv/bin/python scripts/backfill_client_user_mirrors.py` (idempotent; `--dry-run` to preview).
- Smoke test user checks run only against a local API started with `RDB_SUPPRESS_INVITE_EMAILS=true uvicorn ...` (skipped with `--base-url`); test users `rdb-smoke-*` are disabled, not deleted.

## UI theme

The Builder uses an M3-style MUI theme (`frontend/core/src/theme.ts`, which also sets `--rdb-*` CSS variables for `frontend/shared`). Brand assets live in `frontend/shared/brand/` (copies in each app's `public/`).

## Other commands

```bash
.venv/bin/ruff check . && .venv/bin/ruff format .   # lint/format
.venv/bin/pytest                                     # tests

# Infra (never deploy without reviewing the diff)
cd infra
export AWS_PROFILE=concrete AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2
npx cdk synth
npx cdk diff
```
