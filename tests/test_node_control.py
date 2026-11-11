"""node_control: remote settings on the node (mode, cd, pause, isolation, lock).

Every surface - the MCP tool, ``rhm`` subcommands and the cabinet - calls
``core.settings.apply``. These tests pin the security rules around it: a model
may throttle the node but never lift read-only, and the remote-control lock can
always be closed but only reopened at the machine.
"""

import json
import os

import pytest

from core import control, mcp_tools, sandbox, settings


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
    control.set_sandbox_state(None)
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=50000,
    )


# ---------------------------------------------------------------------------
# Remote-control lock
# ---------------------------------------------------------------------------

def test_remote_control_is_on_by_default():
    assert control.remote_control_enabled() is True
    assert control.remote_control_state()["enabled"] is True


def test_remote_control_can_be_closed_from_a_tool_call():
    result = mcp_tools.call_tool("node_control",
                                 {"action": "remote_control", "enabled": False})
    assert "error" not in result
    assert control.remote_control_enabled() is False
    assert "OFF" in result["message"]


def test_a_closed_lock_refuses_every_other_remote_action():
    mcp_tools.call_tool("node_control", {"action": "remote_control", "enabled": False})

    refused = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "read_only"})
    assert "remote_control_disabled" in refused["error"]

    paused = mcp_tools.call_tool("node_control", {"action": "pause"})
    assert "remote_control_disabled" in paused["error"]

    # Reading the switch is how the cabinet explains "why is everything off".
    status = mcp_tools.call_tool("node_control", {"action": "status"})
    assert status["state"]["remote_control"]["enabled"] is False


def test_a_closed_lock_can_never_be_reopened_remotely():
    settings.apply({"action": "remote_control", "enabled": False}, actor="cli")

    for origin in ("mcp", "web"):
        attempt = mcp_tools.call_tool(
            "node_control", {"action": "remote_control", "enabled": True, "_origin": origin})
        assert "remote_control_disabled" in attempt["error"]
        assert "rhm remote-control on" in attempt["error"]
        assert control.remote_control_enabled() is False

    # ... but the machine can, and then remote calls work again.
    reopened = settings.apply({"action": "remote_control", "enabled": True}, actor="cli")
    assert "error" not in reopened
    assert control.remote_control_enabled() is True


def test_closing_an_already_closed_lock_is_not_an_error():
    settings.apply({"action": "remote_control", "enabled": False}, actor="cli")
    again = mcp_tools.call_tool("node_control",
                                {"action": "remote_control", "enabled": False})
    assert "error" not in again


# ---------------------------------------------------------------------------
# Access mode
# ---------------------------------------------------------------------------

def test_read_only_can_be_switched_on_remotely():
    result = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "read_only"})
    assert result["state"]["access_mode"] == "read_only"

    # The rest of the tool surface now behaves read-only: observation works...
    listing = mcp_tools.call_tool("list_dir", {})
    assert "error" not in listing
    # ... commands and writes are refused centrally.
    refused = mcp_tools.call_tool("bash_exec", {"command": "echo hi"})
    assert "error" in refused


def test_a_model_cannot_lift_read_only():
    mcp_tools.call_tool("node_control", {"action": "mode", "mode": "read_only"})

    attempt = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "full"})
    assert "read_only" in attempt["error"]
    assert control.access_mode() == "read_only"


def test_the_cabinet_can_lift_read_only():
    mcp_tools.call_tool("node_control", {"action": "mode", "mode": "read_only"})

    # The gateway stamps _origin=web for a logged-in person; a model's arguments
    # never keep that key (see tests/test_account_control.py for the gateway side).
    unlocked = mcp_tools.call_tool(
        "node_control", {"action": "mode", "mode": "full", "_origin": "web"})
    assert "error" not in unlocked
    assert control.access_mode() == "full"


def test_unknown_mode_is_refused_with_the_allowed_values():
    result = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "root"})
    assert "one of" in result["error"]
    assert control.access_mode() == "full"


# ---------------------------------------------------------------------------
# Working directory
# ---------------------------------------------------------------------------

def test_cd_moves_the_default_directory_of_every_other_tool(tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    (target / "marker.txt").write_text("inside", encoding="utf-8")

    result = mcp_tools.call_tool("node_control", {"action": "cd", "path": str(target)})
    assert "error" not in result
    assert result["workspace"] == os.path.realpath(str(target))

    listing = mcp_tools.call_tool("list_dir", {})
    assert "marker.txt" in listing["stdout"]

    read = mcp_tools.call_tool("read_file", {"path": "marker.txt"})
    assert "inside" in read["stdout"]


def test_cd_to_a_missing_directory_is_refused():
    result = mcp_tools.call_tool("node_control", {"action": "cd", "path": "/nope/nowhere"})
    assert "not a directory" in result["error"]


def test_cd_cannot_leave_an_isolated_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    control.set_sandbox_state({
        "enabled": True, "workspace": str(project), "runtime": "docker",
        "container": "rhm-sandbox-test",
    })

    outside = mcp_tools.call_tool("node_control", {"action": "cd", "path": str(tmp_path)})
    assert "isolation is on" in outside["error"]

    inside = mcp_tools.call_tool("node_control", {"action": "cd", "path": str(project)})
    assert "error" not in inside


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------

def test_a_paused_node_still_answers_status_and_resume():
    settings.apply({"action": "pause"}, actor="cli")

    assert "error" not in mcp_tools.call_tool("node_control", {"action": "status"})
    assert "node_paused" in mcp_tools.call_tool("list_dir", {})["error"]

    resumed = mcp_tools.call_tool("node_control", {"action": "resume"})
    assert "error" not in resumed
    assert control.paused() is False
    assert "error" not in mcp_tools.call_tool("list_dir", {})


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def test_isolation_can_be_started_and_stopped_remotely(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(sandbox, "available", lambda: True)
    started = []

    def fake_enable(workspace, network=False):
        started.append(workspace)
        state = {"enabled": True, "workspace": workspace, "runtime": "docker",
                 "container": "rhm-sandbox-test"}
        control.set_sandbox_state(state)          # what sandbox.enable really does
        return state

    monkeypatch.setattr(sandbox, "enable", fake_enable)
    monkeypatch.setattr(sandbox, "disable", lambda state=None: control.set_sandbox_state(None))

    result = mcp_tools.call_tool("node_control",
                                 {"action": "sandbox", "enabled": True,
                                  "workspace": str(project)})
    assert "error" not in result
    assert started == [os.path.realpath(str(project))]
    assert result["state"]["sandbox"]["enabled"] is True

    stopped = mcp_tools.call_tool("node_control", {"action": "sandbox", "enabled": False})
    assert "error" not in stopped
    assert control.sandbox_state() is None


def test_isolation_is_refused_in_read_only(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(sandbox, "available", lambda: True)
    settings.apply({"action": "mode", "mode": "read_only"}, actor="cli")

    result = mcp_tools.call_tool("node_control",
                                 {"action": "sandbox", "enabled": True,
                                  "workspace": str(project)})
    assert "read_only" in result["error"]


def test_isolation_reports_a_missing_runtime(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(sandbox, "available", lambda: False)

    result = mcp_tools.call_tool("node_control",
                                 {"action": "sandbox", "enabled": True,
                                  "workspace": str(project)})
    assert "Docker or Podman" in result["error"]


# ---------------------------------------------------------------------------
# Approval mode: escalation waits for the operator, the rest does not
# ---------------------------------------------------------------------------

def test_full_access_waits_for_approval_in_approval_mode():
    settings.apply({"action": "mode", "mode": "approval"}, actor="cli")

    queued = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "full"})
    assert "approval_required" in queued["error"]
    assert control.access_mode() == "approval"
    assert control.pending_approvals()

    # Throttling does not need a second trip: read-only is the safe direction.
    throttled = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "read_only"})
    assert "error" not in throttled


def test_approving_once_lets_the_escalation_through():
    settings.apply({"action": "mode", "mode": "approval"}, actor="cli")
    queued = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "full"})
    request_id = (control.pending_approvals()[-1])["id"]

    assert "approval_required" in queued["error"]
    assert control.approve(request_id, seconds=30)
    granted = mcp_tools.call_tool("node_control", {"action": "mode", "mode": "full"})
    assert "error" not in granted
    assert control.access_mode() == "full"


# ---------------------------------------------------------------------------
# Audit and dispatch details
# ---------------------------------------------------------------------------

def test_every_change_is_written_to_the_settings_log():
    mcp_tools.call_tool("node_control", {"action": "pause"})
    path = os.path.join(control.control_dir(), "settings.log")
    assert os.path.exists(path)
    lines = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    assert any(entry["action"] == "pause" and entry["actor"] == "mcp" for entry in lines)


def test_unknown_action_lists_the_actions():
    result = mcp_tools.call_tool("node_control", {"action": "reboot"})
    assert "unknown action" in result["error"]
    assert "sandbox" in result["error"]
