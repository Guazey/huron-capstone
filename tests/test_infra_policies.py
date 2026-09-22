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
    for name in ("deployer-policy.template.json", "boundary-policy.template.json"):
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
