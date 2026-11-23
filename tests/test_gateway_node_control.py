"""Gateway side of ``node_control``: forwarding, origin stamping and claim.

The node's own rules live in tests/test_node_control.py. What the gateway must
get right is the trust boundary: a model may never smuggle the cabinet's
``_origin=web`` (the key is stripped from every client argument), while the
cabinet bridge stamps it on its own calls, and a device is linked only after the
caller proves possession of its token or link key.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from starlette.requests import Request

import gateway

NODE = "node-one"
TOKEN = "0123456789abcdef0123456789abcdef"
LINK = "lk_0123456789abcdef0123456789abcdef"


def _post_request(path, body, headers=None, path_params=None):
    raw = json.dumps(body).encode()
    raw_headers = [(b"host", b"mcp.example.test"),
                   (b"content-type", b"application/json"),
                   (b"accept", b"application/json, text/event-stream")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "headers": raw_headers,
        "query_string": urlparse(path).query.encode(),
        "scheme": "https",
        "server": ("mcp.example.test", 443),
        "client": ("127.0.0.1", 12345),
    }
    if path_params:
        scope["path_params"] = path_params

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


def test_initialize_never_injects_a_node_wide_goal(relay):
    """Goal is opt-in and node-wide, so the handshake does not carry one."""
    relay.result = {"ok": True, "goal": {"text": "finish the deploy",
                                         "status": "active"}, "active": True}
    instructions = _initialize(relay.result)
    assert "ACTIVE GOAL" not in instructions
    assert "finish the deploy" not in instructions


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


# ---------------------------------------------------------------------------
# P1-A: the registry snapshot and ownership recorded on claim
# ---------------------------------------------------------------------------

def _write_registry(data):
    Path(gateway.REGISTRY_PATH).write_text(json.dumps(data), encoding="utf-8")


def _read_registry():
    return json.loads(Path(gateway.REGISTRY_PATH).read_text(encoding="utf-8"))


def test_the_devices_snapshot_carries_metadata_but_no_keys(relay):
    _write_registry({NODE: {
        "token_sha256": gateway.hash_secret(TOKEN),
        "link_key": LINK,
        "hostname": "node-one-pc",
        "os": "Windows",
        "arch": "AMD64",
        "owner_subject": "google:abc",
        "account_id": "acc-1",
        "last_seen": "2026-11-19T07:00:00+00:00",
    }})

    snapshot = gateway.CabinetNodeControl().devices()
    entry = snapshot[NODE]
    assert entry["hostname"] == "node-one-pc"
    assert entry["os"] == "Windows"
    assert entry["arch"] == "AMD64"
    assert entry["owner_subject"] == "google:abc"
    assert entry["account_id"] == "acc-1"
    assert entry["last_seen"] == "2026-11-19T07:00:00+00:00"
    for secret in ("token", "token_sha256", "link_key", "link_key_sha256",
                   "link_key_prev_sha256"):
        assert secret not in entry, "the cabinet snapshot must never carry %r" % secret


def test_the_devices_snapshot_reflects_the_tunnel_state(relay, monkeypatch):
    bridge = gateway.CabinetNodeControl()
    assert bridge.devices()[NODE]["online"] is False
    monkeypatch.setattr(gateway, "active_tunnels",
                        {NODE: {"ws": object(), "pending": {}}})
    assert bridge.devices()[NODE]["online"] is True


def test_the_snapshot_falls_back_to_the_node_name_for_a_hostname(relay):
    _write_registry({NODE: {"token_sha256": gateway.hash_secret(TOKEN)}})
    assert gateway.CabinetNodeControl().devices()[NODE]["hostname"] == NODE


def test_claim_records_the_owner_in_the_registry(relay):
    bridge = gateway.CabinetNodeControl()
    result = bridge.claim(NODE, TOKEN, owner_subject="google:abc", account_id="acc-1")

    assert result == {"ok": True, "node": NODE}
    saved = _read_registry()
    assert saved[NODE]["owner_subject"] == "google:abc"
    assert saved[NODE]["account_id"] == "acc-1"
    # Recording the owner must not disturb the credentials it just verified.
    assert saved[NODE]["token_sha256"] == gateway.hash_secret(TOKEN)
    assert saved[NODE]["link_key"] == LINK


def test_a_rejected_credential_writes_no_owner(relay):
    bridge = gateway.CabinetNodeControl()
    assert bridge.claim(NODE, "nope", owner_subject="google:abc",
                        account_id="acc-1")["ok"] is False
    saved = _read_registry()
    assert "owner_subject" not in saved[NODE]
    assert "account_id" not in saved[NODE]


def test_claim_without_an_owner_leaves_the_registry_alone(relay):
    bridge = gateway.CabinetNodeControl()
    assert bridge.claim(NODE, LINK)["ok"] is True
    saved = _read_registry()
    assert "owner_subject" not in saved[NODE]
    assert "account_id" not in saved[NODE]


def test_claim_cannot_move_an_owned_node_to_another_account(relay):
    """A valid key proves access, not the right to steal another tenant's node."""
    _write_registry({NODE: {
        "token_sha256": gateway.hash_secret(TOKEN),
        "owner_subject": "google:first",
        "account_id": "acc-1",
    }})
    result = gateway.CabinetNodeControl().claim(
        NODE, TOKEN, owner_subject="google:second", account_id="acc-2")

    assert result == {"ok": False, "error": "already_linked"}
    saved = _read_registry()
    assert saved[NODE]["owner_subject"] == "google:first"
    assert saved[NODE]["account_id"] == "acc-1"


# ---------------------------------------------------------------------------
# P1-B: the cabinet approves a pairing code
# ---------------------------------------------------------------------------

PAIR_BODY = {"hostname": "desk-pc", "os": "Linux", "arch": "x86_64"}


@pytest.fixture()
def pair_code(relay):
    """A pending pairing that nobody has approved yet."""
    gateway.pending_pairs.clear()
    gateway._rate_windows.clear()
    response = asyncio.run(gateway.device_pair_start(
        _post_request("/v1/devices/pair", PAIR_BODY)))
    assert response.status_code == 201
    try:
        yield json.loads(response.body)["pair_code"]
    finally:
        gateway.pending_pairs.clear()
        gateway._rate_windows.clear()


def test_the_pair_metadata_carries_no_credentials(pair_code):
    """The browser asks "connect desk-pc?"; it never receives a key."""
    metadata = gateway.CabinetNodeControl().pair(pair_code)

    assert metadata["hostname"] == "desk-pc"
    assert metadata["os"] == "Linux"
    assert metadata["arch"] == "x86_64"
    assert metadata["approved"] is False
    assert metadata["owner_subject"] == ""
    assert metadata["created_at"]
    assert metadata["expires_in"] > 0
    assert set(metadata) == {"hostname", "os", "arch", "created_at", "expires_in",
                             "approved", "owner_subject"}
    for secret in ("token", "token_sha256", "link_key", "link_key_sha256",
                   "device_secret", "issued"):
        assert secret not in metadata


def test_an_unknown_pair_code_has_no_metadata():
    assert gateway.CabinetNodeControl().pair("NOPE-0000") is None


def test_approve_pair_creates_the_registry_entry_with_its_owner(pair_code):
    result = gateway.CabinetNodeControl().approve_pair(
        pair_code, owner_subject="google:abc", account_id="acc-1")

    assert result["ok"] is True
    assert result["username"] == "desk-pc"
    assert "token" not in result and "link_key" not in result
    saved = _read_registry()
    entry = saved["desk-pc"]
    assert entry["owner_subject"] == "google:abc"
    assert entry["account_id"] == "acc-1"
    assert entry["hostname"] == "desk-pc"
    assert entry["os"] == "Linux"
    assert entry["arch"] == "x86_64"
    assert entry["token_sha256"] and entry["link_key_sha256"]
    # The credentials wait for the device that asked for the code, exactly once.
    issued = gateway.pending_pairs[pair_code]["issued"]
    assert issued["username"] == "desk-pc"
    assert issued["link_key"].startswith("lk_")
    assert issued["token"] not in issued["sse_url"]


def test_a_second_approval_is_refused(pair_code):
    bridge = gateway.CabinetNodeControl()
    assert bridge.approve_pair(pair_code, owner_subject="google:abc")["ok"] is True

    again = bridge.approve_pair(pair_code, owner_subject="google:other",
                                account_id="acc-2")
    assert again == {"ok": False, "error": "already_approved"}
    saved = _read_registry()
    assert saved["desk-pc"]["owner_subject"] == "google:abc"
    assert "desk-pc-2" not in saved, "a refused approval must not create a node"


def test_approving_an_unknown_code_is_refused(relay):
    result = gateway.CabinetNodeControl().approve_pair(
        "NOPE-0000", owner_subject="google:abc", account_id="acc-1")
    assert result == {"ok": False, "error": "unknown_pair"}
    assert "desk-pc" not in _read_registry()


def test_an_anonymous_admin_approval_records_no_owner(pair_code, monkeypatch):
    """The admin API may approve without an account, exactly as before."""
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", "admin-secret-0123456789abcdef0123456789")

    response = asyncio.run(gateway.device_pair_approve(
        _post_request("/v1/devices/pair/%s/approve" % pair_code,
                      {"username": "workstation"},
                      headers={"x-admin-token": "admin-secret-0123456789abcdef0123456789"},
                      path_params={"code": pair_code})))

    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "approved", "username": "workstation"}
    saved = _read_registry()
    assert saved["workstation"]["hostname"] == "desk-pc"
    assert "owner_subject" not in saved["workstation"]
    assert "account_id" not in saved["workstation"]


def test_the_admin_api_still_answers_404_and_409(pair_code, monkeypatch):
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", "admin-secret-0123456789abcdef0123456789")
    headers = {"x-admin-token": "admin-secret-0123456789abcdef0123456789"}

    missing = asyncio.run(gateway.device_pair_approve(
        _post_request("/v1/devices/pair/NOPE-0000/approve", {},
                      headers=headers, path_params={"code": "NOPE-0000"})))
    assert missing.status_code == 404
    assert json.loads(missing.body) == {"error": "unknown_pair"}

    approved = asyncio.run(gateway.device_pair_approve(
        _post_request("/v1/devices/pair/%s/approve" % pair_code, {},
                      headers=headers, path_params={"code": pair_code})))
    assert approved.status_code == 200

    again = asyncio.run(gateway.device_pair_approve(
        _post_request("/v1/devices/pair/%s/approve" % pair_code, {},
                      headers=headers, path_params={"code": pair_code})))
    assert again.status_code == 409
    assert json.loads(again.body) == {"error": "already_approved"}

