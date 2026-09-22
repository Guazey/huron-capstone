#!/usr/bin/env bash
# Ask the deployed agent a question, exactly the way the side panel will:
# Cognito login -> bearer token -> POST to the runtime -> read the SSE stream.
#
# Usage: infra/invoke.sh "How is NVDA doing today?"
#        SESSION_ID=<same id> infra/invoke.sh "follow-up"   (reuse a session)
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh
source "$OUTPUTS"

PROMPT="${1:?usage: infra/invoke.sh \"question\"}"

read -rp "Sidebar login email: " USER_EMAIL
read -rsp "Password: " USER_PASSWORD; echo
# JSON, not shorthand, so a password containing "," or "=" still works.
AUTH_PARAMS=$(USER_EMAIL="$USER_EMAIL" USER_PASSWORD="$USER_PASSWORD" python3 -c \
  'import json, os; print(json.dumps({"USERNAME": os.environ["USER_EMAIL"], "PASSWORD": os.environ["USER_PASSWORD"]}))')
TOKEN=$(aws cognito-idp initiate-auth --client-id "$CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH --auth-parameters "$AUTH_PARAMS" \
  --query AuthenticationResult.AccessToken --output text)
unset USER_PASSWORD AUTH_PARAMS

# AgentCore requires session IDs of at least 33 characters.
SESSION_ID="${SESSION_ID:-sidebar-$(uuidgen | tr 'A-Z' 'a-z')}"
ENCODED_ARN=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$RUNTIME_ARN")
BODY=$(python3 -c 'import json, sys; print(json.dumps({"prompt": sys.argv[1]}))' "$PROMPT")

echo "session: $SESSION_ID"
curl -sN "https://bedrock-agentcore.${REGION}.amazonaws.com/runtimes/${ENCODED_ARN}/invocations?qualifier=DEFAULT" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: ${SESSION_ID}" \
  -d "$BODY"
echo

apply_log_retention "$RUNTIME_ID"
