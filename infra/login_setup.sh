#!/usr/bin/env bash
# Wire Cognito's hosted login to the Chrome extension, then write the
# extension's build config. Safe to rerun; deploy.sh calls it at the end.
#
#   1. a Cognito domain for the hosted login page
#   2. the app client allows the OAuth code flow back to the extension
#   3. extension/.env.local (gitignored): region, runtime ARN, client, domain
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh
source "$OUTPUTS"

# The extension ID is derived from the public key pinned in its manifest, so
# the redirect URL below never changes between machines or reloads.
EXTENSION_ID=$(python3 - <<'EOF'
import base64, hashlib, json
key = json.load(open("extension/public/manifest.json"))["key"]
digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
print(digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop")))
EOF
)
REDIRECT_URL="https://${EXTENSION_ID}.chromiumapp.org/"

step() { printf '\n== %s\n' "$*"; }

step "1/3 Cognito hosted login domain"
DOMAIN=$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" \
  --query UserPool.Domain --output text)
if [[ "$DOMAIN" == "None" ]]; then
  # Prefixes are global per region; a hash of the account keeps it unique
  # without putting the account ID in a URL.
  DOMAIN="${NAME}-$(printf '%s' "$ACCOUNT_ID" | shasum | cut -c1-8)"
  aws cognito-idp create-user-pool-domain --user-pool-id "$POOL_ID" \
    --domain "$DOMAIN" --managed-login-version 1 >/dev/null
fi
LOGIN_HOST="https://${DOMAIN}.auth.${REGION}.amazoncognito.com"
echo "  $LOGIN_HOST"

step "2/3 app client: code flow + PKCE back to the extension"
# update-user-pool-client resets anything not passed, so restate every setting.
aws cognito-idp update-user-pool-client --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" \
  --client-name "$NAME" \
  --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
  --access-token-validity 60 --id-token-validity 60 --refresh-token-validity 7 \
  --token-validity-units 'AccessToken=minutes,IdToken=minutes,RefreshToken=days' \
  --prevent-user-existence-errors ENABLED --enable-token-revocation \
  --supported-identity-providers COGNITO \
  --callback-urls "$REDIRECT_URL" --logout-urls "$REDIRECT_URL" \
  --allowed-o-auth-flows code --allowed-o-auth-scopes openid email \
  --allowed-o-auth-flows-user-pool-client >/dev/null
echo "  redirect: $REDIRECT_URL"

step "3/3 extension/.env.local"
cat > extension/.env.local <<EOF
VITE_REGION=${REGION}
VITE_RUNTIME_ARN=${RUNTIME_ARN}
VITE_CLIENT_ID=${CLIENT_ID}
VITE_LOGIN_HOST=${LOGIN_HOST}
EOF
echo "  written. Build with: cd extension && npm install && npm run build"
