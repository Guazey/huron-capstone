#!/usr/bin/env bash
# Build the agent image and deploy it to AgentCore Runtime behind Cognito login.
# Safe to rerun: every step reuses what already exists, and a rerun ships a new
# image version to the same runtime.
#
# Usage: infra/deploy.sh            (Docker Desktop must be running)
# Creates, in $REGION:
#   ECR repo, IAM execution role, Cognito user pool + app client,
#   AgentCore runtime (HTTP protocol, JWT authorizer), log retention policy.
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh

step() { printf '\n== %s\n' "$*"; }

# ---------------------------------------------------------------- 1. image
step "1/6 ECR repo + ARM64 image"
aws ecr describe-repositories --repository-names "$NAME" >/dev/null 2>&1 ||
  aws ecr create-repository --repository-name "$NAME" \
    --image-tag-mutability IMMUTABLE \
    --image-scanning-configuration scanOnPush=true >/dev/null

# Tag with the commit plus a timestamp so every deploy is a new immutable tag.
TAG="$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)"
aws ecr get-login-password | docker login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
docker buildx build --platform linux/arm64 -t "${ECR_REPO}:${TAG}" --push .
IMAGE="${ECR_REPO}:${TAG}"
echo "pushed $IMAGE"

# ---------------------------------------------------------------- 2. IAM role
step "2/6 IAM execution role (least privilege)"
TRUST=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{
  "Effect":"Allow",
  "Principal":{"Service":"bedrock-agentcore.amazonaws.com"},
  "Action":"sts:AssumeRole",
  "Condition":{
    "StringEquals":{"aws:SourceAccount":"${ACCOUNT_ID}"},
    "ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:*"}}}]}
EOF
)
# The us. inference profile routes to several US regions, so the underlying
# foundation-model ARN needs a region wildcard; the profile itself does not.
FOUNDATION_MODEL="${MODEL_ID#us.}"
POLICY=$(cat <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"PullImage","Effect":"Allow",
  "Action":["ecr:BatchGetImage","ecr:GetDownloadUrlForLayer"],
  "Resource":"arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/${NAME}"},
 {"Sid":"EcrAuth","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Sid":"InvokeOneModel","Effect":"Allow",
  "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
  "Resource":[
   "arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:inference-profile/${MODEL_ID}",
   "arn:aws:bedrock:*::foundation-model/${FOUNDATION_MODEL}"]},
 {"Sid":"OwnLogs","Effect":"Allow",
  "Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams"],
  "Resource":"arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"},
 {"Sid":"DescribeLogGroups","Effect":"Allow","Action":"logs:DescribeLogGroups","Resource":"*"},
 {"Sid":"Traces","Effect":"Allow",
  "Action":["xray:PutTraceSegments","xray:PutTelemetryRecords","xray:GetSamplingRules","xray:GetSamplingTargets"],
  "Resource":"*"},
 {"Sid":"Metrics","Effect":"Allow","Action":"cloudwatch:PutMetricData","Resource":"*",
  "Condition":{"StringEquals":{"cloudwatch:namespace":"bedrock-agentcore"}}}]}
EOF
)
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" >/dev/null
  echo "created role; waiting for IAM to propagate"
  sleep 10
fi
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "${NAME}-runtime" --policy-document "$POLICY"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# ---------------------------------------------------------------- 3. Cognito
step "3/6 Cognito user pool + app client (admin-created users only)"
POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 \
  --query "UserPools[?Name=='${NAME}'].Id | [0]" --output text)
if [[ "$POOL_ID" == "None" ]]; then
  POOL_ID=$(aws cognito-idp create-user-pool --pool-name "$NAME" \
    --username-attributes email \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --policies 'PasswordPolicy={MinimumLength=12,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true,RequireSymbols=false}' \
    --query UserPool.Id --output text)
fi
CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
  --query "UserPoolClients[?ClientName=='${NAME}'].ClientId | [0]" --output text)
if [[ "$CLIENT_ID" == "None" ]]; then
  # Public client (no secret): it will live in a browser extension.
  # USER_PASSWORD_AUTH is for invoke.sh testing; slice 4 adds the hosted UI + PKCE.
  CLIENT_ID=$(aws cognito-idp create-user-pool-client --user-pool-id "$POOL_ID" \
    --client-name "$NAME" --no-generate-secret \
    --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --access-token-validity 60 --id-token-validity 60 --refresh-token-validity 7 \
    --token-validity-units 'AccessToken=minutes,IdToken=minutes,RefreshToken=days' \
    --prevent-user-existence-errors ENABLED \
    --query UserPoolClient.ClientId --output text)
fi
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}/.well-known/openid-configuration"

# ---------------------------------------------------------------- 4. runtime
step "4/6 AgentCore runtime"
# Cognito access tokens carry client_id (not aud), so authorize on allowedClients.
AUTHORIZER="{\"customJWTAuthorizer\":{\"discoveryUrl\":\"${DISCOVERY_URL}\",\"allowedClients\":[\"${CLIENT_ID}\"]}}"
COMMON_ARGS=(
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${IMAGE}\"}}"
  --role-arn "$ROLE_ARN"
  --network-configuration '{"networkMode":"PUBLIC"}'
  --protocol-configuration '{"serverProtocol":"HTTP"}'
  --authorizer-configuration "$AUTHORIZER"
  --environment-variables "{\"BEDROCK_MODEL_ID\":\"${MODEL_ID}\"}"
)
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text)
if [[ "$RUNTIME_ID" == "None" ]]; then
  RUNTIME_ID=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "$RUNTIME_NAME" "${COMMON_ARGS[@]}" \
    --query agentRuntimeId --output text)
else
  aws bedrock-agentcore-control update-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" "${COMMON_ARGS[@]}" >/dev/null
fi

step "5/6 wait for READY"
for _ in $(seq 1 60); do
  STATUS=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
    --query status --output text)
  echo "  status: $STATUS"
  case "$STATUS" in
    READY) break ;;
    *FAILED*) aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
                --query failureReason --output text; exit 1 ;;
  esac
  sleep 10
done
[[ "$STATUS" == "READY" ]] || { echo "timed out waiting for READY"; exit 1; }
RUNTIME_ARN=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
  --query agentRuntimeArn --output text)

# ---------------------------------------------------------------- 5. logs
step "6/6 log retention (${LOG_RETENTION_DAYS} days)"
apply_log_retention "$RUNTIME_ID"

cat > "$OUTPUTS" <<EOF
REGION=${REGION}
RUNTIME_ID=${RUNTIME_ID}
RUNTIME_ARN=${RUNTIME_ARN}
POOL_ID=${POOL_ID}
CLIENT_ID=${CLIENT_ID}
IMAGE=${IMAGE}
EOF
printf '\nDeployed. Wrote %s.\nFirst time: infra/create_user.sh, then infra/invoke.sh "How is NVDA doing today?"\n' "$OUTPUTS"

# Hosted login for the Chrome extension + its build config.
infra/login_setup.sh
