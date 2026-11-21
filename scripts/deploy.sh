#!/usr/bin/env bash
# Single deploy path for RDB. Safe to rerun; steps whose resources don't exist yet are skipped.
#
#   scripts/deploy.sh                 # full run
#   SKIP_CDK=1 scripts/deploy.sh      # only frontend/ECS steps (infra unchanged)
#   RDB_REQUIRE_APPROVAL=never scripts/deploy.sh   # no IAM-change prompt (default: broadening)
#
# Optional CloudFormation outputs this script looks for (added by later phases):
#   BuilderBucketName, ViewerBucketName, BuilderDistributionId, ViewerDistributionId,
#   EcsClusterName, EcsServiceName, ApiUrl
set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_REGION="${AWS_REGION:-us-east-2}"
export AWS_DEFAULT_REGION="$AWS_REGION"
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  PROFILE_ARGS=()                      # CI authenticates via OIDC (Phase 7)
else
  export AWS_PROFILE=concrete          # never any other profile locally
  PROFILE_ARGS=(--profile concrete)
fi

log() { printf '\n==> %s\n' "$*"; }

# out KEY -> value of a CloudFormation output from any rdb-* stack, or empty string.
out() {
  local v
  v=$(aws cloudformation describe-stacks "${PROFILE_ARGS[@]}" \
    --query "Stacks[?starts_with(StackName,'rdb-')].Outputs[?OutputKey=='$1'].OutputValue | [0][0]" \
    --output text 2>/dev/null || true)
  [[ "$v" == "None" ]] && v=""
  printf '%s' "$v"
}

# 1. Infra
if [[ "${SKIP_CDK:-0}" != "1" ]]; then
  log "cdk deploy (rdb-* stacks, $AWS_REGION)"
  (cd infra && npm install --no-audit --no-fund --silent \
    && npx cdk deploy "rdb-*" "${PROFILE_ARGS[@]}" \
      --require-approval "${RDB_REQUIRE_APPROVAL:-broadening}")
else
  log "SKIP_CDK=1: skipping cdk deploy"
fi

# 2. Local .env from stack outputs (not in CI)
if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
  log "updating .env from stack outputs"
  .venv/bin/python scripts/write_env.py
fi

# 3. Frontends: build + sync + invalidate (only if the bucket exists)
export VITE_COGNITO_USER_POOL_ID="$(out UserPoolId)"
export VITE_COGNITO_APP_CLIENT_ID="$(out AppClientId)"
export VITE_AWS_REGION="$AWS_REGION"
api_url="$(out ApiUrl)"
[[ -n "$api_url" ]] && export VITE_API_URL="$api_url"

deploy_frontend() { # name Bucket Dist
  local name="$1" bucket="$2" dist="$3"
  if [[ -z "$bucket" ]]; then
    log "$name: no bucket output yet, skipping"
    return
  fi
  log "$name: build + sync to s3://$bucket"
  (cd frontend && npm install --no-audit --no-fund --silent && npm run build --workspace "@rdb/$name")
  aws s3 sync "frontend/$name/dist" "s3://$bucket" "${PROFILE_ARGS[@]}" --delete
  if [[ -n "$dist" ]]; then
    log "$name: CloudFront invalidation ($dist)"
    aws cloudfront create-invalidation --distribution-id "$dist" --paths '/*' "${PROFILE_ARGS[@]}" >/dev/null
  fi
}
deploy_frontend builder "$(out BuilderBucketName)" "$(out BuilderDistributionId)"
deploy_frontend viewer "$(out ViewerBucketName)" "$(out ViewerDistributionId)"

# 4. ECS service (force new deployment so it picks up the latest image)
cluster="$(out EcsClusterName)"
service="$(out EcsServiceName)"
if [[ -n "$cluster" && -n "$service" ]]; then
  log "ECS: rolling $service on $cluster"
  aws ecs update-service --cluster "$cluster" --service "$service" --force-new-deployment \
    "${PROFILE_ARGS[@]}" >/dev/null
else
  log "ECS: no cluster/service yet, skipping"
fi

log "done"
