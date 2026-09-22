#!/usr/bin/env bash
# Create a sidebar login in the Cognito pool deploy.sh made, or set a new
# password on one that already exists. Interactive: run it yourself so the
# password never lands in a log, a transcript, or argv.
# Usage: infra/create_user.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh
source "$OUTPUTS"

read -rp "Email for the sidebar login: " USER_EMAIL
read -rsp "Password (12+ chars, upper, lower, number): " USER_PASSWORD; echo

EXISTING=$(aws cognito-idp list-users --user-pool-id "$POOL_ID" \
  --filter "email = \"${USER_EMAIL}\"" --query 'length(Users)' --output text)
if [[ "$EXISTING" != "0" ]]; then
  echo "user exists; setting the password"
else
  aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$USER_EMAIL" \
    --user-attributes Name=email,Value="$USER_EMAIL" Name=email_verified,Value=true \
    --message-action SUPPRESS >/dev/null
fi

# The password goes in through a private temp file, not argv. (The AWS CLI
# can't read --cli-input-json from stdin: file:///dev/stdin is "Invalid JSON".)
REQUEST=$(mktemp)
chmod 600 "$REQUEST"
trap 'rm -f "$REQUEST"' EXIT
USER_EMAIL="$USER_EMAIL" USER_PASSWORD="$USER_PASSWORD" POOL_ID="$POOL_ID" python3 -c '
import json, os
print(json.dumps({"UserPoolId": os.environ["POOL_ID"], "Username": os.environ["USER_EMAIL"],
                  "Password": os.environ["USER_PASSWORD"], "Permanent": True}))' > "$REQUEST"
unset USER_PASSWORD
aws cognito-idp admin-set-user-password --cli-input-json "file://$REQUEST"
echo "ready: $USER_EMAIL"
