#!/usr/bin/env bash
# Delete everything deploy.sh created. Asks before doing anything.
# Usage: infra/teardown.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh

RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text)
POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 \
  --query "UserPools[?Name=='${NAME}'].Id | [0]" --output text)

cat <<EOF
This permanently deletes, in ${REGION}:
  AgentCore runtime   ${RUNTIME_NAME} (${RUNTIME_ID})
  Cognito user pool   ${NAME} (${POOL_ID}) and its users
  AgentCore Memory    ${MEMORY_NAME} (all saved chats)
  Knowledge Base      ${KB_NAME}, bucket ${FILINGS_BUCKET}, role ${KB_ROLE_NAME}
  IAM role            ${ROLE_NAME}
  ECR repo            ${NAME} and every image in it
  Log groups          /aws/bedrock-agentcore/runtimes/${RUNTIME_ID}*
EOF
read -rp "Type the runtime name (${RUNTIME_NAME}) to confirm: " CONFIRM
[[ "$CONFIRM" == "$RUNTIME_NAME" ]] || { echo "aborted"; exit 1; }

if [[ "$RUNTIME_ID" != "None" ]]; then
  aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" >/dev/null
  echo "deleted runtime"
  for group in $(aws logs describe-log-groups \
      --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}" \
      --query 'logGroups[].logGroupName' --output text); do
    aws logs delete-log-group --log-group-name "$group" && echo "deleted $group"
  done
fi
MEMORY_ID=$(aws bedrock-agentcore-control list-memories \
  --query "memories[?starts_with(id, '${MEMORY_NAME}-')].id | [0]" --output text)
if [[ "$MEMORY_ID" != "None" ]]; then
  aws bedrock-agentcore-control delete-memory --memory-id "$MEMORY_ID" >/dev/null && echo "deleted memory (all chat history)"
fi
KB_ID=$(aws bedrock-agent list-knowledge-bases \
  --query "knowledgeBaseSummaries[?name=='${KB_NAME}'].knowledgeBaseId | [0]" --output text)
if [[ "$KB_ID" != "None" ]]; then
  for ds in $(aws bedrock-agent list-data-sources --knowledge-base-id "$KB_ID" \
      --query 'dataSourceSummaries[].dataSourceId' --output text); do
    aws bedrock-agent delete-data-source --knowledge-base-id "$KB_ID" --data-source-id "$ds" >/dev/null
  done
  aws bedrock-agent delete-knowledge-base --knowledge-base-id "$KB_ID" >/dev/null && echo "deleted knowledge base"
fi
if aws s3api head-bucket --bucket "$FILINGS_BUCKET" >/dev/null 2>&1; then
  aws s3 rm "s3://${FILINGS_BUCKET}" --recursive --quiet
  aws s3api delete-bucket --bucket "$FILINGS_BUCKET" && echo "deleted filings bucket"
fi
if aws iam get-role --role-name "$KB_ROLE_NAME" >/dev/null 2>&1; then
  aws iam delete-role-policy --role-name "$KB_ROLE_NAME" --policy-name "${KB_ROLE_NAME}-s3-read" || true
  aws iam delete-role --role-name "$KB_ROLE_NAME" && echo "deleted KB role"
fi
if [[ "$POOL_ID" != "None" ]]; then
  aws cognito-idp delete-user-pool --user-pool-id "$POOL_ID" && echo "deleted user pool"
fi
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "${NAME}-runtime" || true
  aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "${NAME}-kb-read" || true
  aws iam delete-role --role-name "$ROLE_NAME" && echo "deleted role"
fi
if aws ecr describe-repositories --repository-names "$NAME" >/dev/null 2>&1; then
  aws ecr delete-repository --repository-name "$NAME" --force >/dev/null && echo "deleted ECR repo"
fi
rm -f "$OUTPUTS"
echo "done"
