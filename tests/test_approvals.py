"""Approval mode: the call waits for the operator instead of ending the task.

The reported bug: in approval mode every action stopped the chat with
"approval_required"; the operator approved, had to tell the model to continue,
and the next action stopped again. A queued call now waits for the operator and
continues inside the SAME call, and "always allow" exists so a whole task runs
without a prompt per step.
"""

import threading
import time

import pytest

from core import control, mcp_tools, settings


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
    control.revoke_grant()
    mcp_tools.configure(
        workspace=__import__("os").getcwd(),
        jobs_dir=__import__("os").path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=50000,
    )


def _approval_mode():
    settings.apply({"action": "mode", "mode": "approval"}, actor="cli")


def test_the_call_waits_and_continues_after_one_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_APPROVAL_WAIT", "6")
    _approval_mode()
    target = tmp_path / "approved.txt"
    box = {}

    def worker():
        box["result"] = mcp_tools.call_tool(
            "write_file", {"path": str(target), "content": "ok"})

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not control.pending_approvals():
        time.sleep(0.05)
    pending = control.pending_approvals()
    assert pending, "the node must queue the call for the operator"
    control.approve(pending[-1]["id"])
    thread.join(8)

    assert "error" not in box["result"], box["result"]
    assert box["result"].get("approved") is True
    assert target.read_text(encoding="utf-8") == "ok"


def test_without_an_answer_the_call_is_reported_pending_not_done(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_APPROVAL_WAIT", "0.6")
    _approval_mode()
    target = tmp_path / "never.txt"

    result = mcp_tools.call_tool("write_file", {"path": str(target), "content": "x"})

    assert "approval_required" in result["error"]
    assert "NOT executed" in result["error"]
    assert result.get("pending_approval") is True
    assert not target.exists()
    assert control.pending_approvals(), "the request must stay queued"


def test_always_allow_lets_the_whole_task_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_APPROVAL_WAIT", "0")
    _approval_mode()
    control.approve_all(0)

    first = mcp_tools.call_tool(
        "write_file", {"path": str(tmp_path / "a.txt"), "content": "a"})
    second = mcp_tools.call_tool(
        "write_file", {"path": str(tmp_path / "b.txt"), "content": "b"})

    assert "error" not in first and "error" not in second
    assert control.grant_status()["active"] is True
    assert control.grant_status()["expires_in"] is None
    assert not control.pending_approvals()


def test_changing_the_access_mode_revokes_always():
    _approval_mode()
    control.approve_all(0)
    assert control.grant_status()["active"] is True

    settings.apply({"action": "mode", "mode": "approval"}, actor="cli")
    assert control.grant_status()["active"] is False, (
        "switching to approval must mean approval again")


def test_revoking_restores_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_APPROVAL_WAIT", "0")
    _approval_mode()
    control.approve_all(0)
    control.revoke_grant()

    result = mcp_tools.call_tool(
        "write_file", {"path": str(tmp_path / "c.txt"), "content": "c"})
    assert "approval_required" in result["error"]


def test_allow_for_a_while_shows_the_remaining_time(monkeypatch):
    _approval_mode()
    control.approve_all(600)
    status = control.grant_status()
    assert status["active"] is True
    assert 500 < status["expires_in"] <= 600


def test_the_cabinet_does_not_need_its_own_approval():
    """The logged-in owner clicking "full" is the decision, not a request."""
    _approval_mode()
    result = mcp_tools.call_tool(
        "node_control", {"action": "mode", "mode": "full", "_origin": "web"})
    assert "error" not in result
    assert control.access_mode() == "full"


def test_a_model_still_waits_to_escalate(monkeypatch):
    monkeypatch.setenv("MESH_APPROVAL_WAIT", "0")
    _approval_mode()
    result = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "full"})
    assert "approval_required" in result["error"]
    assert control.access_mode() == "approval"


def test_read_only_tools_never_queue():
    _approval_mode()
    assert "error" not in mcp_tools.call_tool("list_dir", {})
    assert "error" not in mcp_tools.call_tool("goal", {"action": "get"})
    assert not control.pending_approvals()


# ---------------------------------------------------------------------------
# Timeouts stay inside the client's ~30 s call budget
# ---------------------------------------------------------------------------

def test_a_bash_timeout_is_shortened_by_the_approval_wait():
    """12 s of waiting plus a 25 s command would be cut by the gateway."""
    clamped = mcp_tools._clamp_timeout_after_wait("bash_exec", {"timeout_sec": 25}, 8.0)
    assert clamped["timeout_sec"] == 17

    with_wait = mcp_tools._clamp_timeout_after_wait("bash_exec", {"timeout_sec": 5}, 8.0)
    assert with_wait["timeout_sec"] == 5

    # Background jobs are not limited by the call budget at all.
    assert mcp_tools._clamp_timeout_after_wait(
        "run_job", {"timeout_sec": 25}, 8.0)["timeout_sec"] == 25


def test_a_bash_timeout_is_never_shortened_below_five_seconds():
    clamped = mcp_tools._clamp_timeout_after_wait("bash_exec", {"timeout_sec": 25}, 30.0)
    assert clamped["timeout_sec"] == 5
