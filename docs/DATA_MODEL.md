# Data Model

## S3 layout (bucket name rdb-<account-id>-<region>-data or similar)

```
clients/{client_slug}/projects/{project_slug}/
  project.json                          project settings: BQ project, connected tables, limits
  schemas/{dataset}.{table}.json        schema snapshot + analyst column descriptions
  views/{view_slug}/
    draft.json                          pointer: {"revision": N}
    published.json                      pointer: {"revision": N, "published_at": ..., "published_by": ...}
    revisions/{N:06d}.json              full view spec, one file per saved revision
cache/{sha256_of_sql_and_params}.json   cached query results (lifecycle-expired)
```

Enable S3 versioning on the data bucket as a safety net. Static frontend assets go in separate bucket(s).

## DynamoDB: single table rdb_main

Keys: PK (string), SK (string). On-demand billing. Add GSIs only if needed.

| Entity | PK | SK | Notes |
|---|---|---|---|
| Client | CLIENT#{client_slug} | META | name, created_at, created_by |
| Project | CLIENT#{client_slug} | PROJECT#{project_slug} | name, status, created_at |
| View | CLIENT#{client_slug} | VIEW#{project_slug}#{view_slug} | name, status, draft_revision, published_revision, updated_at |
| Access | USER#{cognito_sub} | CLIENT#{client_slug} | which clients a user may access (for client users); `created_at` |
| Access mirror | CLIENT#{client_slug} | USER#{cognito_sub} | reverse of the access item: `email` (lowercase), `added_by` (sub), `added_at`. Always written/deleted together with the access item (TransactWriteItems). Lists a client's users without a scan. |

Access patterns to support: list all clients (add a simple GSI or a CLIENTS listing item if scanning becomes awkward; a scan is fine at prototype scale), list projects for a client, list views for a project, check a user's client access.

Slugs: lowercase letters, numbers, hyphens; unique within their parent; immutable after creation (prototype simplification).

## Cognito

- Groups: admin, analyst, client. A user is in EXACTLY ONE group. Client users are never converted to staff or the reverse.
- Users are never deleted, only disabled (`AdminDisableUser` + `AdminUserGlobalSignOut`). Staff emails must be on `RDB_STAFF_EMAIL_DOMAINS`.
- Client users are mapped to allowed client slug(s) via the Access items above (and/or a token claim).
- admin can create clients and manage users; analyst can build; client can only read published views of assigned clients.
