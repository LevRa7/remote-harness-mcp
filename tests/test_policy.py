"""Session policy tests: mode/scopes before any UI toggle exists."""

import pytest

from core import mcp_tools, policy


def test_read_only_can_inspect_but_not_execute():
    ctx = policy.SessionContext(mode="read_only")
    assert policy.tool_allowed("read_file", ctx)
    assert policy.tool_allowed("grep_search", ctx)
    assert not policy.tool_allowed("bash_exec", ctx)
    assert not policy.tool_allowed("write_file", ctx)


def test_agent_mode_requires_the_matching_scope():
    shell = policy.SessionContext(mode="agent", scopes=("files:read", "shell:exec"))
    assert policy.tool_allowed("bash_exec", shell)
    assert not policy.tool_allowed("device_control", shell)

    device = policy.SessionContext(mode="agent", scopes=("device:control",))
    assert policy.tool_allowed("device_control", device)
    assert not policy.tool_allowed("bash_exec", device)


def test_global_read_only_wins_over_scopes():
    ctx = policy.SessionContext(mode="agent", scopes=("files:read", "shell:exec"))
    assert not policy.tool_allowed("bash_exec", ctx, global_read_only=True)
    assert policy.tool_allowed("read_file", ctx, global_read_only=True)


def test_unknown_mode_and_scope_are_rejected():
    with pytest.raises(ValueError):
        policy.SessionContext(mode="root")
    with pytest.raises(ValueError):
        policy.SessionContext(mode="agent", scopes=("root:everything",))


def test_read_only_ignores_extra_scopes():
    ctx = policy.SessionContext(mode="read_only", scopes=("files:read", "shell:exec"))
    assert ctx.scopes == ("files:read",)


def test_node_guard_and_policy_agree_on_read_only():
    """The node's hard guard and the future cabinet policy must not drift."""
    assert policy.READ_ONLY_TOOLS == mcp_tools._READ_ONLY_ALLOWED - {"_http_share", "device_control", "device_capture", "device_messages"}
