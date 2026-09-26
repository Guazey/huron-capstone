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
# The terminal-only client requires its secret (read from AWS, never stored)
# as a SECRET_HASH. JSON, not shorthand, so any password characters work.
CLI_SECRET=$(aws cognito-idp describe-user-pool-client --user-pool-id "$POOL_ID" \
  --client-id "$CLI_CLIENT_ID" --query UserPoolClient.ClientSecret --output text)
AUTH_PARAMS=$(USER_EMAIL="$USER_EMAIL" USER_PASSWORD="$USER_PASSWORD" CLI_SECRET="$CLI_SECRET" \
  CLI_CLIENT_ID="$CLI_CLIENT_ID" python3 -c '
import base64, hashlib, hmac, json, os
e = os.environ
digest = hmac.new(e["CLI_SECRET"].encode(), (e["USER_EMAIL"] + e["CLI_CLIENT_ID"]).encode(), hashlib.sha256).digest()
print(json.dumps({"USERNAME": e["USER_EMAIL"], "PASSWORD": e["USER_PASSWORD"],
                  "SECRET_HASH": base64.b64encode(digest).decode()}))')
unset CLI_SECRET
AUTH=$(aws cognito-idp initiate-auth --client-id "$CLI_CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH --auth-parameters "$AUTH_PARAMS" --output json)
unset USER_PASSWORD
CHALLENGE=$(jq -r '.ChallengeName // empty' <<<"$AUTH")
if [[ "$CHALLENGE" == "MFA_SETUP" ]]; then
  echo "No authenticator app on this login yet. Sign in once in the side panel or"
  echo "desktop app to scan the QR code, then rerun this." >&2
  exit 1
elif [[ "$CHALLENGE" == "SOFTWARE_TOKEN_MFA" ]]; then
  read -rp "Authenticator code: " MFA_CODE
  # Same SECRET_HASH as the password step; the challenge answers go in as JSON.
  RESPONSES=$(AUTH_PARAMS="$AUTH_PARAMS" MFA_CODE="$MFA_CODE" python3 -c '
import json, os
p = json.loads(os.environ["AUTH_PARAMS"])
print(json.dumps({"USERNAME": p["USERNAME"], "SECRET_HASH": p["SECRET_HASH"],
                  "SOFTWARE_TOKEN_MFA_CODE": os.environ["MFA_CODE"]}))')
  AUTH=$(aws cognito-idp respond-to-auth-challenge --client-id "$CLI_CLIENT_ID" \
    --challenge-name SOFTWARE_TOKEN_MFA --session "$(jq -r .Session <<<"$AUTH")" \
    --challenge-responses "$RESPONSES" --output json)
elif [[ -n "$CHALLENGE" ]]; then
  echo "unexpected sign-in challenge: $CHALLENGE" >&2
  exit 1
fi
TOKEN=$(jq -r .AuthenticationResult.AccessToken <<<"$AUTH")
unset AUTH_PARAMS RESPONSES AUTH

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
