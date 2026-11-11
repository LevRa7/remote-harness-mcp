"""Timers on the node: a long task continues after the chat's call budget ends.

The client gives one tool call ~30 s and an MCP server cannot start a chat turn,
so the timer runs on the node, fires a background job and leaves a
*continuation* the next turn picks up.
"""

import time

import pytest

from core import control, goal, mcp_tools, schedule, settings


@pytest.fixture(autouse=True)
def configured(tmp_path):
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=20000,
    )
    yield
    mcp_tools.configure(
        workspace=__import__("os").getcwd(),
        jobs_dir=__import__("os").path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=50000,
    )


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def test_a_once_timer_is_not_due_before_its_time():
    entry = schedule.add("wake", after_sec=60)
    assert schedule.due() == []
    assert schedule.summary()["active"] == 1
    assert schedule.summary()["next_due_in_sec"] > 50
    assert entry["kind"] == "after"


def test_firing_a_once_timer_creates_a_continuation():
    entry = schedule.add("wake", note="check the download", after_sec=0.05)
    time.sleep(0.12)
    due = schedule.due()
    assert [item["id"] for item in due] == [entry["id"]]

    fired = schedule.mark_fired(entry["id"], job_id="job-1", result="started job job-1")
    assert fired["status"] == "done"

    pending = schedule.pendings()
    assert len(pending) == 1
    assert pending[0]["job_id"] == "job-1"
    assert pending[0]["label"] == "wake"
    assert schedule.summary()["pending_continuations"] == 1

    assert schedule.ack(pending[0]["id"]) == 1
    assert schedule.pendings() == []


def test_an_every_timer_rolls_forward_and_stays_active():
    entry = schedule.add("poll", every_sec=10)
    schedule.mark_fired(entry["id"])
    item = schedule.list_all()["items"][0]
    assert item["status"] == "active"
    assert item["fired_count"] == 1
    assert item["due_at"] > time.time()
    assert schedule.pendings()[0]["schedule_id"] == entry["id"]


def test_a_daily_at_timer_is_within_a_day():
    entry = schedule.add("daily", at="09:30")
    assert entry["kind"] == "at"
    assert time.time() < entry["due_at"] <= time.time() + 86400


def test_a_timer_needs_exactly_one_valid_time_specification():
    with pytest.raises(ValueError):
        schedule.add("x")
    with pytest.raises(ValueError):
        schedule.add("x", every_sec=5)
    with pytest.raises(ValueError):
        schedule.add("x", at="25:99")
    with pytest.raises(ValueError):
        schedule.add("x", after_sec=-1)
    with pytest.raises(ValueError):
        schedule.add("")


def test_cancel_stops_a_timer():
    entry = schedule.add("x", after_sec=30)
    assert schedule.cancel(entry["id"]) is True
    assert schedule.due() == []
    assert schedule.cancel("missing") is False


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

def test_the_tool_sets_lists_and_cancels():
    created = mcp_tools.call_tool("schedule", {
        "action": "set", "label": "wake", "after_sec": 30, "note": "check it"})
    assert created["ok"] is True
    assert created["schedule"]["kind"] == "after"

    listing = mcp_tools.call_tool("schedule", {"action": "list"})
    assert len(listing["schedules"]) == 1
    assert listing["schedules"][0]["label"] == "wake"

    schedule_id = listing["schedules"][0]["id"]
    cancelled = mcp_tools.call_tool("schedule", {"action": "cancel", "id": schedule_id})
    assert cancelled["ok"] is True
    assert mcp_tools.call_tool("schedule", {"action": "list"})["schedules"][0]["status"] == "cancelled"


def test_the_tool_acknowledges_continuations():
    entry = schedule.add("wake", after_sec=0.05)
    time.sleep(0.12)
    schedule.mark_fired(entry["id"], result="reminder")
    pending = mcp_tools.call_tool("schedule", {"action": "list"})["pending"]
    assert pending

    acked = mcp_tools.call_tool("schedule", {"action": "ack", "id": pending[0]["id"]})
    assert acked["removed"] == 1
    assert mcp_tools.call_tool("schedule", {"action": "list"})["pending"] == []


def test_an_unknown_action_lists_the_actions():
    result = mcp_tools.call_tool("schedule", {"action": "explode"})
    assert "unknown action" in result["error"]
    assert "cancel" in result["error"]


def test_mesh_status_reports_the_schedule():
    schedule.add("wake", after_sec=30)
    status = mcp_tools.call_tool("mesh_status", {})
    assert status["schedule"]["active"] == 1


# ---------------------------------------------------------------------------
# Gates: reading is free, creating a deferred command is not
# ---------------------------------------------------------------------------

def test_read_only_can_list_but_cannot_create():
    settings.apply({"action": "mode", "mode": "read_only"}, actor="cli")
    assert "error" not in mcp_tools.call_tool("schedule", {"action": "list"})

    refused = mcp_tools.call_tool(
        "schedule", {"action": "set", "label": "x", "after_sec": 10})
    assert "error" in refused


def test_approval_mode_queues_creation_but_not_listing(monkeypatch):
    monkeypatch.setenv("MESH_APPROVAL_WAIT", "0")
    settings.apply({"action": "mode", "mode": "approval"}, actor="cli")

    queued = mcp_tools.call_tool(
        "schedule", {"action": "set", "label": "x", "after_sec": 10})
    assert "approval_required" in queued["error"]
    assert "error" not in mcp_tools.call_tool("schedule", {"action": "list"})


# ---------------------------------------------------------------------------
# The agent's firing loop
# ---------------------------------------------------------------------------

def test_the_agent_starts_a_due_command_as_a_job(monkeypatch):
    from core import agent

    calls = []

    def fake_call(name, args):
        calls.append((name, dict(args)))
        return {"job_id": "job-1", "status": "running"}

    monkeypatch.setattr(agent.mcp_tools, "call_tool", fake_call)
    entry = schedule.add("wake", command="echo hi", after_sec=0.05)
    time.sleep(0.12)

    fired = agent.fire_due_schedules()

    assert [item["id"] for item in fired] == [entry["id"]]
    assert calls and calls[0][0] == "run_job"
    assert calls[0][1]["command"] == "echo hi"
    pending = schedule.pendings()
    assert pending[0]["job_id"] == "job-1"
    assert "started job job-1" in pending[0]["result"]


def test_a_refused_scheduled_command_is_recorded_not_raised(monkeypatch):
    from core import agent

    monkeypatch.setattr(agent.mcp_tools, "call_tool",
                        lambda name, args: {"error": "read-only"})
    schedule.add("wake", command="echo hi", after_sec=0.05)
    time.sleep(0.12)

    agent.fire_due_schedules()

    pending = schedule.pendings()
    assert pending[0]["job_id"] == ""
    assert "read-only" in pending[0]["result"]
