# Architecture

## Components

- **Builder frontend (React)**: served from S3 + CloudFront at builder.radar-analytics.com.
- **Viewer frontend (React)**: one static app served from S3 + CloudFront on a wildcard subdomain *.radar-analytics.com. A CloudFront Function maps the host's client slug (and the first path segment, the project slug) so the app knows which client and project to load.
- **API (FastAPI)**: Python. Deployed to Fargate for streaming and long-running routes (LLM chat, query execution) and to Lambda for small stateless routes where it is cheap and simple. Locally, everything runs under one uvicorn process.
- **Cognito**: user pool with groups admin, analyst, client. Client users carry their allowed client slug(s) (custom attribute or per-client groups, whichever is simpler; record the choice in DECISIONS.md).
- **S3**: bucket(s) for view specs, revisions, schema snapshots, and query cache. See DATA_MODEL.md.
- **DynamoDB**: single table for the index of clients, projects, views, and user access.
- **Bedrock**: LLM for chat-to-spec and chat-to-SQL.
- **Secrets Manager**: existing secret `GBQ_lambda_secrets` (env `RDB_GBQ_SECRET_NAME`), key `GLOBAL_GBQ_KEY` (env `RDB_GBQ_SECRET_KEY`), holds the full Google service account JSON (value may be a JSON string or an already-parsed object; loader in `backend/app/gbq_credentials.py` handles both). Read it at runtime; never write to it; never print or log values. The account is global and likely sees many clients' data, so the per-project table allowlist and SQL validation are the security boundary.
- **BigQuery**: read-only access to connected tables.

## Request flows

### Chat to chart (Builder)

1. Browser sends the prompt, project ID, view ID, and current spec to the API (streaming response).
2. API builds LLM context: connected tables, schema snapshots, column descriptions, current spec, the prompt.
3. LLM returns SQL plus a chart definition (structured output matching VIEW_SPEC.md).
4. API validates the SQL (below), dry-runs it, runs it with a byte cap, and returns the spec element plus preview data.
5. Frontend renders it with the shared spec renderer.

### Viewing (Viewer)

1. Browser requests a view by client slug, project slug, and view slug. Cognito token required.
2. API checks the user may access that client, loads published.json.
3. For each element, API checks the cache; on miss it validates and runs the saved SQL, writes the result to the cache, and returns data.
4. The browser never receives or sends SQL.

## SQL safety (all SQL, generated or hand-edited)

- Parse with sqlglot (BigQuery dialect).
- Allow a single SELECT statement only. Reject DDL, DML, scripts, multiple statements.
- Only tables connected to that project may be referenced (fully qualified allowlist from project settings).
- BigQuery dry run first to catch errors and estimate bytes.
- Set maximum_bytes_billed on every job. Use a conservative default; make it configurable per project.
- Set a query timeout.

## Caching

- Cache query results in S3 keyed by a hash of (validated SQL + parameters), with a TTL (default 1 hour, configurable per element).
- Builder preview can bypass the cache with a "refresh" action.
- S3 lifecycle rule expires cache objects.

## Revisions and publishing

- Save writes a new revision file and updates the draft pointer.
- Publish copies the current draft revision reference to the published pointer.
- Keep the last 10 revisions per view; prune older ones on save (only inside our own prefix).
- See DATA_MODEL.md for keys.

## Hosting and cost approach

Serverless first. Smallest Fargate task size for the API. S3 + CloudFront for static apps. DynamoDB on-demand. No NAT gateway unless unavoidable (ask first). Prototype with CloudFront and load balancer default domains; custom domains and wildcard certs come later on request.

## Infra as code

AWS CDK in Python under infra/. Stack names rdb-..., resources named rdb_... / rdb-..., all tagged Project=RDB. See AWS_CONVENTIONS.md.
