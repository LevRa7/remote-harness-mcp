"""Gateway side of ``node_control``: forwarding, origin stamping and claim.

The node's own rules live in tests/test_node_control.py. What the gateway must
get right is the trust boundary: a model may never smuggle the cabinet's
``_origin=web`` (the key is stripped from every client argument), while the
cabinet bridge stamps it on its own calls, and a device is linked only after the
caller proves possession of its token or link key.
"""

import asyncio
import json
from urllib.parse import urlparse

import pytest
from starlette.requests import Request

import gateway

NODE = "node-one"
TOKEN = "0123456789abcdef0123456789abcdef"
LINK = "lk_0123456789abcdef0123456789abcdef"


def _post_request(path, body):
    raw = json.dumps(body).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "headers": [(b"host", b"smart-server.online"),
                    (b"content-type", b"application/json"),
                    (b"accept", b"application/json, text/event-stream")],
        "query_string": urlparse(path).query.encode(),
        "scheme": "https",
        "server": ("smart-server.online", 443),
        "client": ("127.0.0.1", 12345),
    }

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


class FakeRelay:
    def __init__(self, result=None):
        self.result = {} if result is None else result
        self.calls = []

    async def __call__(self, user, name, args):
        self.calls.append((user, name, args))
        return self.result


@pytest.fixture()
def relay(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({NODE: {"token_sha256": gateway.hash_secret(TOKEN),
                                       "link_key": LINK}}))
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(path))
    fake = FakeRelay()
    monkeypatch.setattr(gateway, "call_remote_tool", fake)
    return fake


def _call_tool(tool, args, result=None):
    # The URL carries the link key: a device token is only accepted in a header.
    response = asyncio.run(gateway.messages_endpoint(
        _post_request(f"/mcp?user={NODE}&token={LINK}",
                      {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}})
    ))
    return json.loads(response.body)["result"]


# ---------------------------------------------------------------------------
# The model's path: reserved keys never reach the node
# ---------------------------------------------------------------------------

def test_a_model_cannot_smuggle_the_cabinet_origin(relay):
    """``_origin=web`` lifted read-only on the node - and a model could send it."""
    _call_tool("node_control", {"action": "mode", "mode": "full", "_origin": "web"})

    assert relay.calls, "the call must still reach the node"
    user, name, forwarded = relay.calls[0]
    assert (user, name) == (NODE, "node_control")
    assert "_origin" not in forwarded
    assert forwarded == {"action": "mode", "mode": "full"}


def test_every_reserved_underscore_key_is_stripped(relay):
    _call_tool("node_control", {"action": "status", "_origin": "web", "_actor": "cli",
                                "_debug": True})
    forwarded = relay.calls[0][2]
    assert forwarded == {"action": "status"}


def test_node_control_result_is_rendered_for_the_model(relay):
    relay.result = {
        "ok": True,
        "message": "access mode is now read_only",
        "state": {
            "access_mode": "read_only",
            "paused": False,
            "workspace": "/home/user/project",
            "sandbox": {"enabled": True, "workspace": "/home/user/project",
                        "container": "rhm-sandbox-abc"},
            "remote_control": {"enabled": True},
        },
    }
    payload = _call_tool("node_control", {"action": "mode", "mode": "read_only"})
    text = payload["content"][0]["text"]
    assert payload["isError"] is False
    assert "access mode is now read_only" in text
    assert "access_mode: read_only" in text
    assert "workspace: /home/user/project" in text
    assert "isolation: ON at /home/user/project" in text
    assert "remote_control: on" in text


def test_a_refusal_is_reported_as_an_error(relay):
    relay.result = {"error": "remote_control_disabled: off"}
    payload = _call_tool("node_control", {"action": "pause"})
    assert payload["isError"] is True
    assert "remote_control_disabled" in payload["content"][0]["text"]


def test_goal_is_forwarded_and_rendered(relay):
    relay.result = {
        "ok": True,
        "action": "set",
        "message": "goal: finish the deploy",
        "goal": {"text": "finish the deploy", "status": "active"},
        "active": True,
    }
    payload = _call_tool("goal", {"action": "set", "text": "finish the deploy"})
    text = payload["content"][0]["text"]
    assert payload["isError"] is False
    assert "goal: finish the deploy" in text
    assert "status: active" in text


def test_goal_closed_shows_the_note(relay):
    relay.result = {
        "ok": True,
        "action": "complete",
        "message": "goal completed",
        "goal": None,
        "history": [{"text": "finish the deploy", "status": "done",
                     "note": "green build"}],
    }
    payload = _call_tool("goal", {"action": "complete", "note": "green build"})
    text = payload["content"][0]["text"]
    assert "goal completed" in text
    assert "last: done - finish the deploy" in text


# ---------------------------------------------------------------------------
# initialize carries the node's objective into the session
# ---------------------------------------------------------------------------

def _initialize(relay_result):
    response = asyncio.run(gateway.messages_endpoint(
        _post_request(f"/mcp?user={NODE}&token={LINK}",
                      {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test"}}})
    ))
    return json.loads(response.body)["result"]["instructions"]


def test_initialize_replays_the_active_goal(relay):
    relay.result = {"ok": True, "goal": {"text": "finish the deploy",
                                         "status": "active"}, "active": True}
    instructions = _initialize(relay.result)
    assert "ACTIVE GOAL" in instructions
    assert "finish the deploy" in instructions


def test_initialize_without_a_goal_has_no_goal_block(relay):
    relay.result = {"ok": True, "goal": None, "active": False}
    instructions = _initialize(relay.result)
    assert "ACTIVE GOAL" not in instructions


def test_initialize_survives_a_node_without_the_goal_tool(relay):
    """An older node answers Unknown tool: the handshake must not fail."""
    relay.result = {"error": "Unknown tool: goal"}
    instructions = _initialize(relay.result)
    assert "Remote Harness MCP node" in instructions
    assert "ACTIVE GOAL" not in instructions


def test_schedule_set_is_forwarded_and_confirmed(relay):
    relay.result = {"ok": True, "action": "set",
                    "message": "timer 'wake' set for 2026-11-11 12:00:00"}
    payload = _call_tool("schedule", {"action": "set", "label": "wake", "after_sec": 30})
    assert payload["isError"] is False
    assert "timer 'wake' set" in payload["content"][0]["text"]


def test_schedule_list_renders_timers_and_firings(relay):
    relay.result = {
        "ok": True,
        "message": "1 active timer(s), 1 pending continuation(s)",
        "schedules": [{"id": "s1", "label": "wake", "kind": "after",
                       "next_due_iso": "2026-11-11 12:00:00", "command": "echo hi"}],
        "pending": [{"id": "c1", "label": "wake", "fired_at_iso": "2026-11-11 11:00:00",
                     "result": "started job j1", "job_id": "j1"}],
    }
    text = _call_tool("schedule", {"action": "list"})["content"][0]["text"]
    assert "wake [s1]" in text
    assert "FIRED wake" in text
    assert "job=j1" in text


def test_initialize_replays_a_fired_timer(relay):
    relay.result = {"ok": True, "schedules": [], "pending": [
        {"id": "c1", "label": "wake", "fired_at_iso": "2026-11-11 11:00:00",
         "result": "started job j1", "job_id": "j1", "note": "check it"}]}
    instructions = _initialize(relay.result)
    assert "SCHEDULED CONTINUATION" in instructions
    assert "wake" in instructions
    assert "started job j1" in instructions
    assert "schedule(action=ack" in instructions


def test_initialize_without_fired_timers_has_no_schedule_block(relay):
    relay.result = {"ok": True, "schedules": [], "pending": []}
    instructions = _initialize(relay.result)
    assert "SCHEDULED CONTINUATION" not in instructions


def test_a_slow_bash_call_is_capped_to_the_client_budget(relay):
    """The gateway gives up at 28 s, so it never forwards a 120 s bash timeout."""
    relay.result = {"exit_code": 0, "stdout": "ok", "stderr": ""}
    _call_tool("bash_exec", {"command": "echo hi", "timeout_sec": 120})
    forwarded = relay.calls[-1][2]
    assert forwarded["timeout_sec"] == 25


# ---------------------------------------------------------------------------
# The cabinet's path: the bridge stamps the origin
# ---------------------------------------------------------------------------

def test_the_cabinet_bridge_stamps_the_web_origin(relay):
    bridge = gateway.CabinetNodeControl()
    asyncio.run(bridge.call(NODE, {"action": "mode", "mode": "full"}))

    forwarded = relay.calls[-1][2]
    assert forwarded["_origin"] == "web"
    assert forwarded["action"] == "mode"


def test_the_cabinet_bridge_defaults_to_status(relay):
    bridge = gateway.CabinetNodeControl()
    asyncio.run(bridge.call(NODE, {}))
    assert relay.calls[-1][2]["action"] == "status"


def test_the_bridge_reports_whether_the_node_is_online(monkeypatch):
    bridge = gateway.CabinetNodeControl()
    monkeypatch.setattr(gateway, "active_tunnels", {NODE: {"ws": object(), "pending": {}}})
    assert bridge.online(NODE) is True
    assert bridge.online("missing") is False


# ---------------------------------------------------------------------------
# Claim: an account name is not an identity
# ---------------------------------------------------------------------------

def test_claim_accepts_the_device_token(relay):
    bridge = gateway.CabinetNodeControl()
    assert bridge.claim(NODE, TOKEN)["ok"] is True


def test_claim_accepts_the_link_key_from_the_mcp_url(relay):
    bridge = gateway.CabinetNodeControl()
    assert bridge.claim(NODE, LINK)["ok"] is True


def test_claim_rejects_a_wrong_credential(relay):
    bridge = gateway.CabinetNodeControl()
    result = bridge.claim(NODE, "lk_deadbeef")
    assert result == {"ok": False, "error": "bad_credential"}


def test_claim_rejects_an_unknown_node(relay):
    bridge = gateway.CabinetNodeControl()
    assert bridge.claim("not-registered", TOKEN) == {"ok": False, "error": "unknown_node"}


def test_claim_does_not_reveal_a_different_error_for_a_wrong_token(relay):
    """Both "unknown node" and "wrong key" must stay uninformative in the page."""
    bridge = gateway.CabinetNodeControl()
    assert bridge.claim("not-registered", TOKEN)["error"] == "unknown_node"
    assert bridge.claim(NODE, "nope")["error"] == "bad_credential"
