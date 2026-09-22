# Shared settings for deploy.sh, invoke.sh, and teardown.sh. Sourced, not run.
# Nothing secret lives here; account ID and resource IDs are looked up at run time.

REGION="${AWS_REGION:-us-west-2}"
MODEL_ID="${BEDROCK_MODEL_ID:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"

NAME="capstone-sidebar"            # ECR repo, IAM role, Cognito pool/client
RUNTIME_NAME="capstone_sidebar"    # AgentCore runtime names allow no hyphens
LOG_RETENTION_DAYS=14
MEMORY_NAME="capstone_sidebar_memory"  # letters, digits, underscores only
MEMORY_EXPIRY_DAYS=7                   # chats are kept a week, then deleted
KB_NAME="${NAME}-sec-filings"
KB_ROLE_NAME="${NAME}-kb"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
# Bucket names are global; a hash of the account keeps it unique and private.
FILINGS_BUCKET="${NAME}-filings-$(printf '%s' "$ACCOUNT_ID" | shasum | cut -c1-8)"
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${NAME}"
ROLE_NAME="${NAME}-runtime"
# Ceiling on what the project roles can ever do; created once by an admin
# (infra/render_policies.sh -> infra/boundary-policy.json).
BOUNDARY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${NAME}-boundary"
CLI_CLIENT_NAME="${NAME}-cli"  # terminal-only login client (infra/invoke.sh)

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
