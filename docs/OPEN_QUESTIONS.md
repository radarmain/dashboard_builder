# Open Questions

Answer these as they come up. Make a sensible assumption and note it in DECISIONS.md if I do not answer.

## Answered (details in DECISIONS.md)

1. AWS region: us-east-2 (config quirk, see DECISIONS.md)
2. CDK bootstrap: CDKToolkit exists in us-east-2, version 32. (Answered)
3. Route 53: no radar-analytics.com zone in this account; only `cpa.local.` exists. DNS is handled elsewhere or later (Phase 8). (Answered)
4. Client access: Cognito group `client` plus DynamoDB Access items (USER#sub / CLIENT#slug). No per-client groups. (Answered)
5. Bedrock model: `us.anthropic.claude-sonnet-5-5` (proposed, in use unless objected to). (Answered)
6. BigQuery: default maximum_bytes_billed = 10 GB, configurable per project. (Answered)

## Still open

- Which BigQuery projects and datasets the service account can see (will be discovered in Phase 3 via the API; no action needed now).
- Where is DNS for radar-analytics.com managed? (Phase 8 only.)
