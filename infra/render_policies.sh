#!/usr/bin/env bash
# Fill the IAM policy templates with this account's IDs, for pasting into the
# IAM console. The rendered files are gitignored: IDs stay out of the public repo.
#
# Usage: infra/render_policies.sh
#   Before the first deploy, the Cognito pool and KB don't exist yet, so those
#   two resources render as "*". Rerun after deploy.sh and update the deployer
#   policy to pin them to this project's pool and knowledge base.
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh
[[ -f "$OUTPUTS" ]] && source "$OUTPUTS"

render() {
  REGION="$REGION" ACCOUNT_ID="$ACCOUNT_ID" POOL_ID="${POOL_ID:-*}" KB_ID="${KB_ID:-*}" \
  python3 - "$1" "$2" <<'PY'
import json, os, re, sys
text = open(sys.argv[1]).read()
for key in ("REGION", "ACCOUNT_ID", "POOL_ID", "KB_ID"):
    text = text.replace("{{" + key + "}}", os.environ[key])
left = re.findall(r"\{\{\w+\}\}", text)
assert not left, f"unfilled placeholders: {left}"
policy = json.loads(text)
size = len(json.dumps(policy, separators=(",", ":")))
assert size <= 6144, f"{sys.argv[2]} is {size} chars; managed policies allow 6,144"
open(sys.argv[2], "w").write(json.dumps(policy, indent=2) + "\n")
print(f"  {sys.argv[2]} ({size} chars)")
PY
}

render infra/boundary-policy.template.json infra/boundary-policy.json
render infra/deployer-policy.template.json infra/deployer-policy.json
echo "Pool pinned: ${POOL_ID:-no (not deployed yet)}; KB pinned: ${KB_ID:-no (not deployed yet)}"
