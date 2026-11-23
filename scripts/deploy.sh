#!/usr/bin/env bash
# Single deploy path for RDB. Safe to rerun; a rerun with no changes uploads/invalidates nothing.
#
#   scripts/deploy.sh          # show `cdk diff`, ask, then deploy rdb-core + rdb-app, build + publish both frontends
#   scripts/deploy.sh --yes    # no prompt (CI uses this)
#   SKIP_CDK=1 scripts/deploy.sh --yes   # frontends only (infra unchanged)
set -euo pipefail
cd "$(dirname "$0")/.."

YES=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    *) echo "unknown argument: $arg (only --yes is supported)" >&2; exit 2 ;;
  esac
done

export AWS_REGION="${AWS_REGION:-us-east-2}"
export AWS_DEFAULT_REGION="$AWS_REGION"
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  PROFILE_ARGS=()                      # CI authenticates via OIDC (role rdb-github-deploy)
else
  export AWS_PROFILE=concrete          # never any other profile locally
  PROFILE_ARGS=(--profile concrete)
fi

log() { printf '\n==> %s\n' "$*"; }

# out STACK KEY -> a CloudFormation output value, or empty string.
out() {
  local v
  v=$(aws cloudformation describe-stacks --stack-name "$1" "${PROFILE_ARGS[@]}" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null || true)
  [[ "$v" == "None" ]] && v=""
  printf '%s' "$v"
}

# infra/config.json holds the ACM cert ARN (scripts/request_cert.py). Empty = default domains only.
CERT_ARN="$(python3 -c 'import json;print(json.load(open("infra/config.json")).get("cert_arn",""))' 2>/dev/null || true)"
if [[ -n "$CERT_ARN" ]]; then log "custom domains: certificate $CERT_ARN"; else log "no cert_arn in infra/config.json: default CloudFront domains only"; fi

# 1. Infra (rdb-core, then rdb-app; the API image is built by CDK from backend/Dockerfile)
if [[ "${SKIP_CDK:-0}" != "1" ]]; then
  (cd infra && npm install --no-audit --no-fund --silent)
  if [[ "$YES" != "1" ]]; then
    log "cdk diff (rdb-core, rdb-app, $AWS_REGION)"
    (cd infra && npx cdk diff rdb-core rdb-app "${PROFILE_ARGS[@]}") || true
    if [[ ! -t 0 ]]; then
      echo "Not a terminal and no --yes: stopping after the diff." >&2
      exit 1
    fi
    read -r -p "Deploy these changes? [y/N] " reply
    [[ "$reply" == "y" || "$reply" == "Y" ]] || { echo "Aborted."; exit 1; }
  fi
  log "cdk deploy rdb-core rdb-app ($AWS_REGION)"
  (cd infra && npx cdk deploy rdb-core rdb-app "${PROFILE_ARGS[@]}" --require-approval never)
else
  log "SKIP_CDK=1: skipping cdk deploy"
fi

# 2. Local .env from stack outputs (not in CI)
if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
  log "updating .env from stack outputs"
  .venv/bin/python scripts/write_env.py
fi

# 3. Frontends: build with public Cognito IDs (API base is relative "/api"), sync, invalidate
export VITE_COGNITO_USER_POOL_ID="$(out rdb-core UserPoolId)"
export VITE_COGNITO_APP_CLIENT_ID="$(out rdb-core AppClientId)"
export VITE_AWS_REGION="$AWS_REGION"
export VITE_VIEWER_URL="$(out rdb-app ViewerUrl)"  # Builder gate links clients here when not on the custom domain
unset VITE_API_URL
[[ -n "$VITE_COGNITO_USER_POOL_ID" && -n "$VITE_COGNITO_APP_CLIENT_ID" ]] \
  || { echo "rdb-core outputs not found; is the stack deployed?" >&2; exit 1; }

log "frontend install + build"
(cd frontend && npm install --no-audit --no-fund --silent && npm run setup --silent)

# publish NAME BUCKET DIST: hashed assets are immutable; everything else is no-cache.
publish() {
  local name="$1" bucket="$2" dist="$3" changed
  [[ -n "$bucket" && -n "$dist" ]] || { echo "$name: bucket/distribution output missing" >&2; exit 1; }
  log "$name: build + sync to s3://$bucket"
  (cd frontend && npm run build --workspace "@rdb/$name" --silent)
  local dir="frontend/$name/dist"
  aws s3 sync "$dir/assets" "s3://$bucket/assets" "${PROFILE_ARGS[@]}" \
    --cache-control "public,max-age=31536000,immutable" --only-show-errors
  local sync_out
  sync_out=$(aws s3 sync "$dir" "s3://$bucket" "${PROFILE_ARGS[@]}" --exclude "assets/*" --delete \
    --cache-control "no-cache")
  changed=$(grep -cE '(upload|delete):' <<<"$sync_out" || true)
  if [[ "$changed" != "0" ]]; then
    log "$name: $changed file(s) changed, invalidating CloudFront ($dist)"
    aws cloudfront create-invalidation --distribution-id "$dist" --paths '/*' "${PROFILE_ARGS[@]}" >/dev/null
  else
    echo "$name: no changes"
  fi
}
publish builder "$(out rdb-app BuilderBucketName)" "$(out rdb-app BuilderDistributionId)"
publish viewer "$(out rdb-app ViewerBucketName)" "$(out rdb-app ViewerDistributionId)"

log "done"
echo "Builder: $(out rdb-app BuilderUrl)"
echo "Viewer:  $(out rdb-app ViewerUrl)"
if [[ -n "$CERT_ARN" ]]; then
  echo "Custom:  https://builder.radar-analytics.com and https://<client>.radar-analytics.com (needs the DNS records in docs/DNS_RECORDS.md)"
fi
