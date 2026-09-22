# Shared settings for deploy.sh, invoke.sh, and teardown.sh. Sourced, not run.
# Nothing secret lives here; account ID and resource IDs are looked up at run time.

REGION="${AWS_REGION:-us-west-2}"
MODEL_ID="${BEDROCK_MODEL_ID:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"

NAME="capstone-sidebar"            # ECR repo, IAM role, Cognito pool/client
RUNTIME_NAME="capstone_sidebar"    # AgentCore runtime names allow no hyphens
LOG_RETENTION_DAYS=14

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${NAME}"
ROLE_NAME="${NAME}-runtime"

# Written by deploy.sh, read by invoke.sh and teardown.sh. Gitignored.
OUTPUTS="$(dirname "${BASH_SOURCE[0]}")/outputs.env"

export AWS_REGION="$REGION"
export AWS_PAGER=""

# Cap how long chat logs live. The runtime creates its log group on first
# start, so deploy.sh and invoke.sh both call this; it's a no-op until then.
apply_log_retention() {
  local group
  for group in $(aws logs describe-log-groups \
      --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/$1" \
      --query 'logGroups[].logGroupName' --output text); do
    aws logs put-retention-policy --log-group-name "$group" \
      --retention-in-days "$LOG_RETENTION_DAYS"
  done
}
