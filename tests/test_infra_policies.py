"""The IAM policy templates: valid, within size limits, scoped, and free of real IDs."""
import json
import pathlib
import re

INFRA = pathlib.Path(__file__).resolve().parent.parent / "infra"
VALUES = {"REGION": "us-west-2", "ACCOUNT_ID": "111122223333", "POOL_ID": "us-west-2_Abc", "KB_ID": "KB12345678"}


def render(name):
    text = (INFRA / name).read_text()
    assert not re.search(r"\b\d{12}\b", text), "a real account ID is in a committed template"
    for key, value in VALUES.items():
        text = text.replace("{{" + key + "}}", value)
    assert "{{" not in text
    return json.loads(text)


def statements(policy):
    return {s["Sid"]: s for s in policy["Statement"]}


def test_templates_render_within_managed_policy_limit():
    for name in ("deployer-policy.template.json", "deployer-ci-policy.template.json", "boundary-policy.template.json"):
        assert len(json.dumps(render(name), separators=(",", ":"))) <= 6144


def test_deployer_can_only_touch_project_roles_with_the_boundary():
    s = statements(render("deployer-policy.template.json"))
    write = s["ProjectRolesOnlyWithBoundary"]
    assert write["Condition"]["StringEquals"]["iam:PermissionsBoundary"].endswith(":policy/capstone-sidebar-boundary")
    assert all(r.endswith(("role/capstone-sidebar-runtime", "role/capstone-sidebar-kb")) for r in write["Resource"])
    assert s["NeverLoosenTheBoundary"]["Effect"] == "Deny"
    assert "iam:DeleteRolePermissionsBoundary" in s["NeverLoosenTheBoundary"]["Action"]


def test_deployer_is_pinned_to_this_pool_and_knowledge_base():
    s = statements(render("deployer-policy.template.json"))
    assert s["CognitoThisPoolOnly"]["Resource"].endswith(":userpool/us-west-2_Abc")
    assert "cognito-idp:SetUserPoolMfaConfig" in s["CognitoThisPoolOnly"]["Action"]
    assert s["KnowledgeBaseThisOneOnly"]["Resource"].endswith(":knowledge-base/KB12345678")


def test_deployer_cannot_invoke_or_read_chats():
    actions = [a for st in render("deployer-policy.template.json")["Statement"] if st["Effect"] == "Allow"
               for a in ([st["Action"]] if isinstance(st["Action"], str) else st["Action"])]
    for forbidden in ("InvokeAgentRuntime", "ListEvents", "GetEvent", "*Memor*", "*AgentRuntime*"):
        assert not any(forbidden in a for a in actions), forbidden


def test_boundary_keeps_roles_to_claude_and_project_resources():
    s = statements(render("boundary-policy.template.json"))
    assert all("anthropic" in r for r in s["ClaudeOnly"]["Resource"])
    assert s["OwnChatMemory"]["Resource"].endswith(":memory/capstone_sidebar_memory-*")
    assert not any(st.get("Action") == "*" for st in s.values())


def test_deploy_requires_authenticator_app_mfa():
    deploy = (INFRA / "deploy.sh").read_text()
    assert "--mfa-configuration ON" in deploy
    assert "--software-token-mfa-configuration Enabled=true" in deploy
    assert "sms-mfa-configuration" not in deploy


def test_ci_deployer_policy_touches_only_the_ci_role_and_githubs_provider():
    s = statements(render("deployer-ci-policy.template.json"))
    assert s["CiRoleOnlyWithBoundary"]["Resource"].endswith(":role/capstone-sidebar-eval-ci")
    assert s["CiRoleOnlyWithBoundary"]["Condition"]["StringEquals"]["iam:PermissionsBoundary"].endswith(
        ":policy/capstone-sidebar-boundary")
    assert s["CiRoleReadTrustAndDelete"]["Resource"].endswith(":role/capstone-sidebar-eval-ci")
    assert s["NeverLoosenTheCiBoundary"]["Effect"] == "Deny"
    assert s["GithubOidcProvider"]["Resource"].endswith(":oidc-provider/token.actions.githubusercontent.com")
    assert "iam:DeleteOpenIDConnectProvider" not in s["GithubOidcProvider"]["Action"]
    actions = [a for st in s.values() if st["Effect"] == "Allow"
               for a in ([st["Action"]] if isinstance(st["Action"], str) else st["Action"])]
    assert not any(a.startswith(("bedrock", "sts", "iam:PassRole", "iam:CreatePolicy")) for a in actions)


def test_ci_role_trusts_only_this_repos_main_branch_and_can_only_invoke_and_search():
    script = (INFRA / "ci_role.sh").read_text()
    assert '"${OIDC_HOST}:sub":"${SUB_PREFIX}:ref:refs/heads/main"' in script
    assert 'SUB_PREFIX="${SUB_PREFIX:-repo:${REPO}}"' in script  # never empty, never a wildcard
    assert '"${OIDC_HOST}:aud":"sts.amazonaws.com"' in script
    assert "StringLike" not in script  # no wildcard subjects (any branch, any PR)
    assert '--permissions-boundary "$BOUNDARY_ARN"' in script
    start = script.index("POLICY=$(cat <<EOF")
    policy = script[start:script.index("EOF\n)", start)]
    assert set(re.findall(r'"(bedrock:\w+)"', policy)) == {
        "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Retrieve"}
    assert "knowledge-base/${KB_ID}" in policy and "knowledge-base/*" not in policy
