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

# Viewer UI -> http://localhost:5174 (Phase 6)
cd frontend/viewer && npm run dev
```

First time only: generate the builder's public Cognito settings (`frontend/builder/.env.local`, gitignored):

```bash
AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/write_env.py
```

Sign in with your Cognito user. A temporary password prompts you to set a new one.

Handy for curl: `TOKEN=$(.venv/bin/python scripts/get_token.py)`; live API check: `scripts/smoke_test.py project.dataset.table`
(creates and deletes temporary Cognito users; see the file header).

Troubleshooting: if `vite build` says `Cannot find module '../lightningcss.darwin-arm64.node'`, run
`cd frontend && npm install --no-save --workspaces=false lightningcss-darwin-arm64@$(node -p "require('./node_modules/lightningcss/package.json').version")`.

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
