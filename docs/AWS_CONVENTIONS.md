# AWS Conventions

- Profile: `concrete` (always). Example: `aws sts get-caller-identity --profile concrete`.
- Region: read from the profile config, confirm with the user once, then use it everywhere. Do not assume.
- Naming: prefix `rdb_` for resources that allow underscores (DynamoDB table rdb_main, Lambda rdb_..., secrets, log groups). Use `rdb-` where underscores are not allowed (S3 buckets, CloudFormation stacks, ECS cluster and service names, some IAM names).
- Tagging: every taggable resource gets `Project=RDB` (set via CDK `Tags.of(app).add("Project", "RDB")`). Optionally also `Owner=william` and `Env=dev`.
- Existing resources we may READ but must never modify: Secrets Manager secret `GBQ_lambda_secrets` (AWS-managed default key, no KMS changes needed; key `GLOBAL_GBQ_KEY` holds the full Google service account JSON), and anything else not created by this project. Our IAM roles are granted `secretsmanager:GetSecretValue` on that secret's ARN only, via our own policies. Never print or log its values. Secret name and key are configurable via `RDB_GBQ_SECRET_NAME` / `RDB_GBQ_SECRET_KEY`.
- IAM: we may create roles for this project (prefix `rdb-`, tightly scoped). Prefer roles over IAM users. Bedrock access is limited to the `us.anthropic.claude-sonnet-5-5` inference profile plus `arn:aws:bedrock:*::foundation-model/anthropic.*` (InvokeModel, InvokeModelWithResponseStream). Cognito admin actions are scoped to the `rdb-users` pool ARN.
- Deploys: `scripts/deploy.sh` is the single deploy path (cdk deploy of rdb stacks, frontend build + S3 sync, CloudFront invalidation, ECS update; steps are skipped if their resources don't exist yet; safe to rerun).
- Never delete or alter non-rdb resources. If a command could affect something that does not have the rdb prefix or tag, stop and ask.
- IaC: CDK (Python) in infra/. Check whether the account and region are already bootstrapped before deploying; ask before bootstrapping.
- Before deploying, show the CDK diff or a short plain-language resource list for anything involving IAM, networking, or recurring cost.
- Cost: serverless and on-demand first; smallest Fargate size; no NAT gateway without asking; set S3 lifecycle rules on cache; CloudWatch log retention 14 days.
- DNS and custom domains (builder.radar-analytics.com, *.radar-analytics.com, ACM certs) are deferred. Do not touch Route 53 or request certificates until asked.
- Teardown for this project must be possible via `cdk destroy` on rdb stacks only; never use account-wide cleanup commands.
