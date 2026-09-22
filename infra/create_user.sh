#!/usr/bin/env bash
# Create a sidebar login in the Cognito pool deploy.sh made. Interactive:
# run it yourself so the password never lands in a log, a transcript, or argv.
# Usage: infra/create_user.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh
source "$OUTPUTS"

read -rp "Email for the sidebar login: " USER_EMAIL
read -rsp "Password (12+ chars, upper, lower, number): " USER_PASSWORD; echo

aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$USER_EMAIL" \
  --user-attributes Name=email,Value="$USER_EMAIL" Name=email_verified,Value=true \
  --message-action SUPPRESS >/dev/null

# Password goes in via a JSON file on stdin, not a command-line argument.
USER_EMAIL="$USER_EMAIL" USER_PASSWORD="$USER_PASSWORD" POOL_ID="$POOL_ID" python3 -c '
import json, os
print(json.dumps({"UserPoolId": os.environ["POOL_ID"], "Username": os.environ["USER_EMAIL"],
                  "Password": os.environ["USER_PASSWORD"], "Permanent": True}))' |
  aws cognito-idp admin-set-user-password --cli-input-json file:///dev/stdin
unset USER_PASSWORD
echo "created $USER_EMAIL"
