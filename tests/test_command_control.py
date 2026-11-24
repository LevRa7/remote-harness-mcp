"""Tray command control: pause and live read-only flag."""

import os
import time

import pytest

from core import control, mcp_tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_CONTROL_DIR", str(tmp_path / "control"))
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )
    yield tmp_path
    control.set_paused(False)
    control.set_read_only(False)
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )


def test_pause_blocks_every_call_until_resumed(workspace):
    sample = workspace / "note.txt"
    sample.write_text("hello", encoding="utf-8")

    control.set_paused(True)
    pressed = mcp_tools.call_tool("read_file", {"path": str(sample)})
    assert pressed["error"].startswith("node_paused")

    control.set_paused(False)
    resumed = mcp_tools.call_tool("read_file", {"path": str(sample)})
    assert "hello" in resumed["stdout"]


def test_read_only_flag_blocks_shell_and_allows_reads(workspace):
    sample = workspace / "note.txt"
    sample.write_text("hello", encoding="utf-8")

    control.set_read_only(True)
    blocked = mcp_tools.call_tool("bash_exec", {"command": "echo nope"})
    assert "error" in blocked
    assert "read-only" in blocked["error"].lower()

    allowed = mcp_tools.call_tool("read_file", {"path": str(sample)})
    assert "hello" in allowed["stdout"]

    control.set_read_only(False)
    assert "stdout" in mcp_tools.call_tool("bash_exec", {"command": "echo ok"})


def test_state_reports_heartbeat_age(workspace):
    heartbeat = workspace / "control" / "agent.heartbeat"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text("beat", encoding="utf-8")
    state = control.state()
    assert state["online"] is True
    assert state["heartbeat_age_sec"] is not None
    assert state["paused"] is False
    assert "token" not in state


def test_missing_heartbeat_is_offline(workspace):
    state = control.state()
    assert state["online"] is False
    assert state["heartbeat_age_sec"] is None


def test_mutating_calls_run_without_a_node_side_prompt(workspace):
    # The client draws the confirmation dialog; the node executes the call.
    result = mcp_tools.call_tool("bash_exec", {"command": "echo free"})
    assert "stdout" in result, result
    assert "approval_required" not in result.get("error", "")


def test_a_legacy_approval_setting_means_full(workspace):
    assert control.set_access_mode("approval") == "full"
    result = mcp_tools.call_tool("bash_exec", {"command": "echo legacy"})
    assert "stdout" in result, result


def test_workspace_override_is_live(workspace):
    target = workspace / "work"
    target.mkdir()
    assert control.set_workspace(str(target)) == str(target)
    assert control.workspace() == str(target)
    sample = target / "inside.txt"
    sample.write_text("inside", encoding="utf-8")
    result = mcp_tools.call_tool("read_file", {"path": "inside.txt"})
    assert "inside" in result["stdout"]


def test_env_read_only_is_the_fallback(workspace, monkeypatch):
    monkeypatch.delenv("MESH_ACCESS_MODE", raising=False)
    monkeypatch.setenv("MESH_READ_ONLY", "1")
    for name in ("access.mode", "readonly.flag"):
        try:
            (workspace / "control" / name).unlink()
        except FileNotFoundError:
            pass
    assert control.access_mode() == "read_only"
    assert "error" in mcp_tools.call_tool("bash_exec", {"command": "echo no"})


def test_explicit_menu_mode_wins_over_env(workspace, monkeypatch):
    """The operator's rhm choice is the permission; the env file is a fallback."""
    monkeypatch.setenv("MESH_READ_ONLY", "1")
    mcp_tools.configure(read_only=True)
    control.set_access_mode("full")
    assert control.has_explicit_mode() is True
    result = mcp_tools.call_tool("bash_exec", {"command": "echo allowed"})
    assert "stdout" in result
    assert "allowed" in result["stdout"]


def test_the_product_default_is_full_access(workspace, monkeypatch):
    monkeypatch.delenv("MESH_ACCESS_MODE", raising=False)
    monkeypatch.delenv("MESH_READ_ONLY", raising=False)
    for name in ("access.mode", "readonly.flag"):
        try:
            (workspace / "control" / name).unlink()
        except FileNotFoundError:
            pass
    assert control.access_mode() == "full"
