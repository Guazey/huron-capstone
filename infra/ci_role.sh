#!/usr/bin/env bash
# Let the GitHub Actions eval workflow (.github/workflows/eval.yml) call
# Bedrock, with no AWS keys stored in GitHub.
#
# GitHub signs a short-lived token (OIDC) for each workflow run; AWS trades it
# for 1-hour credentials on this role, and only for runs of this repository's
# main branch. The role can call the one model and search the one knowledge
# base, nothing else, and the project boundary caps it even if this policy
# were loosened.
#
# Usage: infra/ci_role.sh     (after deploy.sh; needs `gh auth login`)
# Creates: the GitHub OIDC identity provider (once per account), the role,
# and the repository secrets and variables the workflow reads.
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/config.sh
source "$OUTPUTS"

REPO="Guazey/huron-capstone"
CI_ROLE_NAME="${NAME}-eval-ci"
OIDC_HOST="token.actions.githubusercontent.com"
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"

step() { printf '\n== %s\n' "$*"; }

SEC_USER_AGENT="${SEC_USER_AGENT:-$(grep -s '^SEC_USER_AGENT=' .env | cut -d= -f2- | sed -E "s/^[\"'](.*)[\"']\$/\1/" || true)}"
[[ "$SEC_USER_AGENT" == *@* ]] || { echo 'Set SEC_USER_AGENT in .env first.' >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo 'Run gh auth login first.' >&2; exit 1; }

step "1/3 GitHub OIDC identity provider"
# AWS validates GitHub's certificate itself, so no thumbprint is needed.
aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1 ||
  aws iam create-open-id-connect-provider --url "https://${OIDC_HOST}" \
    --client-id-list sts.amazonaws.com >/dev/null
echo "  $OIDC_HOST"

step "2/3 role ${CI_ROLE_NAME}"
# Only this repo's main branch: the token's sub claim is <prefix>:ref:<ref>.
# Pull requests (including from forks) and other branches get no credentials.
# GitHub reports the prefix: with immutable subjects it carries the owner and
# repo IDs (repo:Guazey@180081659/huron-capstone@1380809494), so a repo
# deleted and re-created under the same name can't use this role.
SUB_PREFIX=$(gh api "repos/${REPO}/actions/oidc/customization/sub" --jq '.sub_claim_prefix // empty' 2>/dev/null || true)
SUB_PREFIX="${SUB_PREFIX:-repo:${REPO}}"
TRUST=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Principal":{"Federated":"${OIDC_ARN}"},
 "Action":"sts:AssumeRoleWithWebIdentity",
 "Condition":{"StringEquals":{
  "${OIDC_HOST}:aud":"sts.amazonaws.com",
  "${OIDC_HOST}:sub":"${SUB_PREFIX}:ref:refs/heads/main"}}}]}
EOF
)
FOUNDATION_MODEL="${MODEL_ID#us.}"
POLICY=$(cat <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"InvokeOneModel","Effect":"Allow",
  "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
  "Resource":[
   "arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:inference-profile/${MODEL_ID}",
   "arn:aws:bedrock:*::foundation-model/${FOUNDATION_MODEL}"]},
 {"Sid":"SearchOneKnowledgeBase","Effect":"Allow","Action":"bedrock:Retrieve",
  "Resource":"arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:knowledge-base/${KB_ID}"}]}
EOF
)
if ! aws iam get-role --role-name "$CI_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$CI_ROLE_NAME" --permissions-boundary "$BOUNDARY_ARN" \
    --assume-role-policy-document "$TRUST" --max-session-duration 3600 >/dev/null
else
  aws iam update-assume-role-policy --role-name "$CI_ROLE_NAME" --policy-document "$TRUST"
fi
aws iam put-role-permissions-boundary --role-name "$CI_ROLE_NAME" --permissions-boundary "$BOUNDARY_ARN"
aws iam put-role-policy --role-name "$CI_ROLE_NAME" --policy-name "${CI_ROLE_NAME}-invoke" \
  --policy-document "$POLICY"
CI_ROLE_ARN=$(aws iam get-role --role-name "$CI_ROLE_NAME" --query Role.Arn --output text)
echo "  can invoke ${MODEL_ID} and search knowledge base ${KB_ID}; trusted by ${SUB_PREFIX}:ref:refs/heads/main only"

step "3/3 GitHub repository secrets and variables"
# Secrets are masked in workflow logs: the role ARN carries the account ID,
# and the user agent carries an email address.
gh secret set AWS_EVAL_ROLE_ARN -R "$REPO" --body "$CI_ROLE_ARN"
gh secret set SEC_USER_AGENT -R "$REPO" --body "$SEC_USER_AGENT"
gh secret set KB_ID -R "$REPO" --body "$KB_ID"
gh variable set AWS_REGION -R "$REPO" --body "$REGION"
gh variable set BEDROCK_MODEL_ID -R "$REPO" --body "$MODEL_ID"
echo "  done. Run it: gh workflow run eval.yml -R ${REPO}"
