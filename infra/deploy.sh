#!/usr/bin/env bash
# Build the agent image and deploy it to AgentCore Runtime behind Cognito login.
# Safe to rerun: every step reuses what already exists, and a rerun ships a new
# image version to the same runtime.
#
# Usage: infra/deploy.sh            (Docker Desktop must be running)
# Creates, in $REGION:
#   ECR repo, IAM execution role, Cognito user pool + app client,
#   AgentCore Memory, S3 bucket + Bedrock Managed Knowledge Base (SEC filings),
#   AgentCore runtime (HTTP protocol, JWT authorizer),
#   log retention policy.
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
step "2/6 IAM execution role (least privilege, inside the permissions boundary)"
if ! aws iam get-policy --policy-arn "$BOUNDARY_ARN" >/dev/null 2>&1; then
  echo "Missing the permissions boundary ${BOUNDARY_ARN##*/}."
  echo "Run infra/render_policies.sh, then as an admin create a customer managed policy"
  echo "named ${BOUNDARY_ARN##*/} from infra/boundary-policy.json."
  exit 1
fi
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
  "Resource":"arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/${RUNTIME_NAME}-*"},
 {"Sid":"DescribeLogGroups","Effect":"Allow","Action":"logs:DescribeLogGroups","Resource":"*"},
 {"Sid":"Traces","Effect":"Allow",
  "Action":["xray:PutTraceSegments","xray:PutTelemetryRecords","xray:GetSamplingRules","xray:GetSamplingTargets"],
  "Resource":"*"},
 {"Sid":"ChatMemory","Effect":"Allow",
  "Action":["bedrock-agentcore:CreateEvent","bedrock-agentcore:GetEvent",
            "bedrock-agentcore:ListEvents","bedrock-agentcore:DeleteEvent"],
  "Resource":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:memory/${MEMORY_NAME}-*"},
 {"Sid":"Metrics","Effect":"Allow","Action":"cloudwatch:PutMetricData","Resource":"*",
  "Condition":{"StringEquals":{"cloudwatch:namespace":"bedrock-agentcore"}}}]}
EOF
)
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --permissions-boundary "$BOUNDARY_ARN" \
    --assume-role-policy-document "$TRUST" >/dev/null
  echo "created role; waiting for IAM to propagate"
  sleep 10
fi
# Also covers roles created before the boundary existed.
aws iam put-role-permissions-boundary --role-name "$ROLE_NAME" --permissions-boundary "$BOUNDARY_ARN"
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
  CLIENT_ID=$(aws cognito-idp create-user-pool-client --user-pool-id "$POOL_ID" \
    --client-name "$NAME" --no-generate-secret \
    --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --access-token-validity 60 --id-token-validity 60 --refresh-token-validity 7 \
    --token-validity-units 'AccessToken=minutes,IdToken=minutes,RefreshToken=days' \
    --prevent-user-existence-errors ENABLED \
    --query UserPoolClient.ClientId --output text)
fi
# Terminal-only client for infra/invoke.sh: password login, but only with its
# secret, and its ID never ships in the extension. The browser client above
# has no direct password login.
CLI_CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
  --query "UserPoolClients[?ClientName=='${CLI_CLIENT_NAME}'].ClientId | [0]" --output text)
if [[ "$CLI_CLIENT_ID" == "None" ]]; then
  CLI_CLIENT_ID=$(aws cognito-idp create-user-pool-client --user-pool-id "$POOL_ID" \
    --client-name "$CLI_CLIENT_NAME" --generate-secret \
    --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --access-token-validity 60 --id-token-validity 60 --refresh-token-validity 1 \
    --token-validity-units 'AccessToken=minutes,IdToken=minutes,RefreshToken=days' \
    --prevent-user-existence-errors ENABLED --enable-token-revocation \
    --query UserPoolClient.ClientId --output text)
fi
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}/.well-known/openid-configuration"

# ---------------------------------------------------------------- memory
step "3b/6 AgentCore Memory (chat history, ${MEMORY_EXPIRY_DAYS}-day expiry)"
# Short-term memory only (no strategies): raw conversation events per session.
MEMORY_ID=$(aws bedrock-agentcore-control list-memories \
  --query "memories[?starts_with(id, '${MEMORY_NAME}-')].id | [0]" --output text)
if [[ "$MEMORY_ID" == "None" ]]; then
  MEMORY_ID=$(aws bedrock-agentcore-control create-memory --name "$MEMORY_NAME" \
    --event-expiry-duration "$MEMORY_EXPIRY_DAYS" \
    --description "Market sidebar chat history" \
    --query memory.id --output text)
fi
for _ in $(seq 1 30); do
  MEMORY_STATUS=$(aws bedrock-agentcore-control get-memory --memory-id "$MEMORY_ID" \
    --query memory.status --output text)
  [[ "$MEMORY_STATUS" == "ACTIVE" ]] && break
  [[ "$MEMORY_STATUS" == *FAILED* ]] && { echo "memory $MEMORY_STATUS"; exit 1; }
  echo "  memory: $MEMORY_STATUS"; sleep 10
done
[[ "$MEMORY_STATUS" == "ACTIVE" ]] || { echo "timed out waiting for memory"; exit 1; }
echo "  $MEMORY_ID"

# ---------------------------------------------------------------- knowledge base
step "3c/6 SEC filings: S3 bucket + Bedrock Managed Knowledge Base"
if ! aws s3api head-bucket --bucket "$FILINGS_BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$FILINGS_BUCKET" \
    --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
fi
aws s3api put-public-access-block --bucket "$FILINGS_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$FILINGS_BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# The KB's connector role: read this bucket, nothing else (managed KBs need
# no vector-store or embedding-model permissions).
KB_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{
  "Effect":"Allow","Principal":{"Service":"bedrock.amazonaws.com"},"Action":"sts:AssumeRole",
  "Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT_ID}"},
               "ArnLike":{"aws:SourceArn":"arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:knowledge-base/*"}}}]}
JSON
)
KB_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::${FILINGS_BUCKET}"},
 {"Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::${FILINGS_BUCKET}/*"}]}
JSON
)
if ! aws iam get-role --role-name "$KB_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$KB_ROLE_NAME" --permissions-boundary "$BOUNDARY_ARN" \
    --assume-role-policy-document "$KB_TRUST" >/dev/null
  echo "  created KB role; waiting for IAM to propagate"; sleep 10
fi
aws iam put-role-permissions-boundary --role-name "$KB_ROLE_NAME" --permissions-boundary "$BOUNDARY_ARN"
aws iam put-role-policy --role-name "$KB_ROLE_NAME" --policy-name "${KB_ROLE_NAME}-s3-read" \
  --policy-document "$KB_POLICY"

KB_ID=$(aws bedrock-agent list-knowledge-bases \
  --query "knowledgeBaseSummaries[?name=='${KB_NAME}'].knowledgeBaseId | [0]" --output text)
if [[ "$KB_ID" == "None" ]]; then
  KB_ID=$(aws bedrock-agent create-knowledge-base --name "$KB_NAME" \
    --description "Recent 10-K and 10-Q filings for the market sidebar" \
    --role-arn "arn:aws:iam::${ACCOUNT_ID}:role/${KB_ROLE_NAME}" \
    --knowledge-base-configuration '{"type":"MANAGED","managedKnowledgeBaseConfiguration":{"embeddingModelType":"MANAGED"}}' \
    --query knowledgeBase.knowledgeBaseId --output text)
fi
for _ in $(seq 1 60); do
  KB_STATUS=$(aws bedrock-agent get-knowledge-base --knowledge-base-id "$KB_ID" \
    --query knowledgeBase.status --output text)
  [[ "$KB_STATUS" == "ACTIVE" ]] && break
  [[ "$KB_STATUS" == *FAILED* ]] && { echo "knowledge base $KB_STATUS"; exit 1; }
  echo "  knowledge base: $KB_STATUS"; sleep 10
done
[[ "$KB_STATUS" == "ACTIVE" ]] || { echo "timed out waiting for the knowledge base"; exit 1; }

# Managed KBs use the managed connector shape, not the classic s3Configuration.
KB_DATA_SOURCE_ID=$(aws bedrock-agent list-data-sources --knowledge-base-id "$KB_ID" \
  --query "dataSourceSummaries[?name=='sec-filings-s3'].dataSourceId | [0]" --output text)
if [[ "$KB_DATA_SOURCE_ID" == "None" ]]; then
  DS_CONFIG=$(cat <<JSON
{"type":"MANAGED_KNOWLEDGE_BASE_CONNECTOR","managedKnowledgeBaseConnectorConfiguration":{
  "connectorParameters":{"type":"S3","version":"1","connectionConfiguration":{
    "bucketName":"${FILINGS_BUCKET}","bucketOwnerAccountId":"${ACCOUNT_ID}"}}}}
JSON
)
  KB_DATA_SOURCE_ID=$(aws bedrock-agent create-data-source --knowledge-base-id "$KB_ID" \
    --name sec-filings-s3 --data-source-configuration "$DS_CONFIG" \
    --query dataSource.dataSourceId --output text)
fi
for _ in $(seq 1 60); do
  DS_STATUS=$(aws bedrock-agent get-data-source --knowledge-base-id "$KB_ID" \
    --data-source-id "$KB_DATA_SOURCE_ID" --query dataSource.status --output text)
  [[ "$DS_STATUS" == "AVAILABLE" ]] && break
  [[ "$DS_STATUS" == *FAIL* || "$DS_STATUS" == "DELETE_UNSUCCESSFUL" ]] && { echo "data source $DS_STATUS"; exit 1; }
  echo "  data source: $DS_STATUS"; sleep 10
done
[[ "$DS_STATUS" == "AVAILABLE" ]] || { echo "timed out waiting for the data source"; exit 1; }
echo "  knowledge base $KB_ID, data source $KB_DATA_SOURCE_ID, bucket $FILINGS_BUCKET"

# The agent may search this knowledge base and no other.
KB_READ=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"bedrock:Retrieve",
  "Resource":"arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:knowledge-base/${KB_ID}"}]}
JSON
)
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "${NAME}-kb-read" --policy-document "$KB_READ"

# ---------------------------------------------------------------- 4. runtime
step "4/6 AgentCore runtime"
# Cognito access tokens carry client_id (not aud), so authorize on allowedClients.
AUTHORIZER="{\"customJWTAuthorizer\":{\"discoveryUrl\":\"${DISCOVERY_URL}\",\"allowedClients\":[\"${CLIENT_ID}\",\"${CLI_CLIENT_ID}\"]}}"
COMMON_ARGS=(
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${IMAGE}\"}}"
  --role-arn "$ROLE_ARN"
  --network-configuration '{"networkMode":"PUBLIC"}'
  --protocol-configuration '{"serverProtocol":"HTTP"}'
  --authorizer-configuration "$AUTHORIZER"
  --environment-variables "{\"BEDROCK_MODEL_ID\":\"${MODEL_ID}\",\"MEMORY_ID\":\"${MEMORY_ID}\",\"KB_ID\":\"${KB_ID}\"}"
  # Forward the (already verified) bearer token so app.py can key memory by user.
  --request-header-configuration '{"requestHeaderAllowlist":["Authorization"]}'
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
CLI_CLIENT_ID=${CLI_CLIENT_ID}
IMAGE=${IMAGE}
MEMORY_ID=${MEMORY_ID}
KB_ID=${KB_ID}
KB_DATA_SOURCE_ID=${KB_DATA_SOURCE_ID}
FILINGS_BUCKET=${FILINGS_BUCKET}
EOF
printf '\nDeployed. Wrote %s.\nFirst time: infra/create_user.sh, then load filings (see sec_edgar.py), then infra/invoke.sh "How is NVDA doing today?"\n' "$OUTPUTS"

# Hosted login for the Chrome extension + its build config.
infra/login_setup.sh
