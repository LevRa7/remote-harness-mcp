"""There is no node-side approval gate.

Confirmation of an MCP call belongs to the client the node is connected to
(Gemini Web, ChatGPT, an IDE): it owns the dialog and it has the operator. The
node used to queue mutating calls for a prompt of its own, and every client
that could not draw that prompt dead-ended on approval_required. These tests
pin the opposite contract: the node executes what it is asked to execute, and
the old mode name only means full access.
"""

import os

import pytest

from core import control, mcp_tools, settings


@pytest.fixture(autouse=True)
def configured_tools(tmp_path):
    control.set_access_mode("full")
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


def test_the_only_modes_are_read_only_and_full():
    assert control.ACCESS_MODES == ("read_only", "full")
    assert "approval" not in control.ACCESS_LABELS


def test_a_mutating_call_runs_immediately(tmp_path):
    target = tmp_path / "runs.txt"
    result = mcp_tools.call_tool("write_file", {"path": str(target), "content": "ok"})
    assert "error" not in result, result
    assert target.read_text(encoding="utf-8") == "ok"


def test_a_legacy_approval_file_means_full(tmp_path):
    control.set_access_mode("approval")
    assert control.access_mode() == "full"


def test_the_legacy_name_is_accepted_and_normalised():
    assert control.set_access_mode("approval") == "full"
    assert control.access_mode() == "full"
    state = settings.apply({"action": "mode", "mode": "approval"}, actor="cli")
    assert state["state"]["access_mode"] == "full"


def test_the_legacy_environment_value_means_full(monkeypatch):
    try:
        os.remove(os.path.join(control.control_dir(), "access.mode"))
    except OSError:
        pass
    monkeypatch.setenv("MESH_ACCESS_MODE", "approval")
    assert control.access_mode() == "full"


def test_there_is_no_approve_tool_any_more():
    assert "approve" not in [tool["name"] for tool in mcp_tools.TOOLS]
    assert "approve" not in mcp_tools._HANDLERS


def test_read_only_still_refuses_a_mutation(tmp_path):
    control.set_access_mode("read_only")
    try:
        result = mcp_tools.call_tool("write_file", {"path": str(tmp_path / "x.txt"), "content": "x"})
        assert "read-only" in result["error"].lower()
    finally:
        control.set_access_mode("full")


def test_a_legacy_setting_does_not_leave_a_queue_directory():
    control.set_access_mode("approval")
    mcp_tools.call_tool("write_file", {"path": "q.txt", "content": "q"})
    pending = os.path.join(control.control_dir(), "pending")
    assert not os.path.isdir(pending) or not os.listdir(pending)
