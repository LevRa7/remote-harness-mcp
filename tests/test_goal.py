"""goal: the persistent objective a chat records on the node.

The point is continuity: a client cannot keep a model working between sessions,
so the node stores the objective and the gateway puts it back into the session
instructions (``tests/test_gateway_node_control.py`` covers that side).
"""

import json
import os

import pytest

from core import control, goal, mcp_tools, settings


@pytest.fixture(autouse=True)
def configured_tools(tmp_path):
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=20000,
    )
    yield
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=50000,
    )


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def test_there_is_no_goal_by_default():
    data = goal.get()
    assert data["goal"] is None
    assert data["active"] is False
    assert "none" in goal.summary_line()


def test_set_then_get_round_trips_and_persists_to_disk():
    goal.set_goal("  bring the   MCP up to date ", actor="mcp")
    data = goal.get()
    assert data["active"] is True
    assert data["goal"]["text"] == "bring the MCP up to date"
    assert data["goal"]["status"] == "active"

    # It is on disk, so an agent restart (a fresh process) reads it back.
    path = os.path.join(control.control_dir(), goal.GOAL_FILE)
    stored = json.loads(open(path, encoding="utf-8").read())
    assert stored["goal"]["text"] == "bring the MCP up to date"


def test_replacing_a_goal_keeps_the_previous_one_in_history():
    goal.set_goal("first")
    goal.set_goal("second")
    data = goal.get()
    assert data["goal"]["text"] == "second"
    assert data["history"][0]["text"] == "first"
    assert data["history"][0]["status"] == "replaced"


def test_completing_moves_the_goal_to_history():
    goal.set_goal("ship it")
    data = goal.finish("done", note="shipped")
    assert data["goal"] is None
    assert data["history"][0]["status"] == "done"
    assert data["history"][0]["note"] == "shipped"


def test_blocked_requires_an_active_goal():
    with pytest.raises(ValueError):
        goal.finish("blocked")
    goal.set_goal("ship it")
    data = goal.finish("blocked", note="waiting for review")
    assert data["history"][0]["status"] == "blocked"
    assert data["history"][0]["note"] == "waiting for review"


def test_empty_text_is_refused():
    with pytest.raises(ValueError):
        goal.set_goal("   ")


def test_clear_forgets_the_active_goal():
    goal.set_goal("temporary")
    data = goal.clear()
    assert data["goal"] is None
    assert data["history"][0]["status"] == "replaced"


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

def test_the_tool_sets_reads_and_closes():
    started = mcp_tools.call_tool("goal", {"action": "set", "text": "fix the build"})
    assert started["active"] is True
    assert started["goal"]["text"] == "fix the build"
    assert started["message"].startswith("goal:")

    read = mcp_tools.call_tool("goal", {"action": "get"})
    assert read["goal"]["text"] == "fix the build"

    closed = mcp_tools.call_tool("goal", {"action": "complete", "note": "green"})
    assert closed["active"] is False
    assert closed["history"][0]["status"] == "done"
    assert closed["message"] == "goal completed"


def test_the_tool_defaults_to_get_and_reports_history():
    mcp_tools.call_tool("goal", {"action": "set", "text": "one"})
    mcp_tools.call_tool("goal", {"action": "set", "text": "two"})
    data = mcp_tools.call_tool("goal", {})
    assert data["goal"]["text"] == "two"
    assert data["history"][0]["text"] == "one"


def test_set_without_text_is_an_error():
    result = mcp_tools.call_tool("goal", {"action": "set"})
    assert "text is required" in result["error"]


def test_an_unknown_action_lists_the_actions():
    result = mcp_tools.call_tool("goal", {"action": "abandon"})
    assert "unknown action" in result["error"]
    assert "complete" in result["error"]


def test_the_goal_survives_read_only_and_needs_no_approval():
    settings.apply({"action": "mode", "mode": "read_only"}, actor="cli")
    result = mcp_tools.call_tool("goal", {"action": "set", "text": "inspect the logs"})
    assert "error" not in result
    assert control.access_mode() == "read_only"

    settings.apply({"action": "mode", "mode": "approval"}, actor="cli")
    queued = mcp_tools.call_tool("goal", {"action": "complete"})
    assert "error" not in queued, "a note is not a system change; it must not wait for approval"
    assert not control.pending_approvals()


def test_mesh_status_carries_the_objective():
    mcp_tools.call_tool("goal", {"action": "set", "text": "finish the deploy"})
    status = mcp_tools.call_tool("mesh_status", {})
    assert status["goal"]["text"] == "finish the deploy"
    assert status["goal_active"] is True
