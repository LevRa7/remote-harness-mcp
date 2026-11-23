"""Hardening of the standalone MCP HTTP server (REV-SEC-14).

The server used to disable authentication when ``MESH_TOKEN`` was unset, sent
``Access-Control-Allow-Origin: *`` and accepted any content type.  A page open in
the user's browser could therefore POST to ``http://127.0.0.1:8096/mcp`` and run
``bash_exec`` (DNS rebinding / CSRF -> RCE).  These tests pin the new default:
a token always exists, the Host/Origin must be loopback, and POST bodies must be
JSON.
"""

import os
import stat

from core import server


def test_loopback_host_is_accepted():
    assert server.host_allowed("127.0.0.1:8096", "127.0.0.1", 8096)
    assert server.host_allowed("localhost:8096", "127.0.0.1", 8096)
    assert server.host_allowed("[::1]:8096", "127.0.0.1", 8096)


def test_foreign_host_is_rejected():
    assert not server.host_allowed("evil.example:8096", "127.0.0.1", 8096)
    assert not server.host_allowed("127.0.0.1.evil.example:8096", "127.0.0.1", 8096)
    assert not server.host_allowed("evil.example", "127.0.0.1", 8096)


def test_loopback_origin_is_accepted():
    assert server.origin_allowed("http://127.0.0.1:8096", "127.0.0.1", 8096)
    assert server.origin_allowed("http://localhost:8096", "127.0.0.1", 8096)


def test_foreign_and_null_origin_are_rejected():
    assert not server.origin_allowed("https://evil.example", "127.0.0.1", 8096)
    assert not server.origin_allowed("null", "127.0.0.1", 8096)
    assert not server.origin_allowed("http://127.0.0.1:9999", "127.0.0.1", 8096)


def test_json_content_type_is_required_for_post():
    assert server.json_content_type("application/json")
    assert server.json_content_type("application/json; charset=utf-8")
    assert not server.json_content_type("text/plain")
    assert not server.json_content_type("application/x-www-form-urlencoded")
    assert not server.json_content_type("")


def test_missing_token_is_generated_and_saved_with_user_only_permissions(tmp_path):
    target = tmp_path / "standalone.token"
    token = server.ensure_auth_token("", path=str(target))
    assert len(token) >= 32
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == token
    if os.name != "nt":
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode & 0o077 == 0, "token file must not be group/world readable"


def test_configured_token_is_returned_unchanged():
    assert server.ensure_auth_token("already-set-token") == "already-set-token"


def test_generated_token_is_reused_on_restart(tmp_path):
    target = tmp_path / "standalone.token"
    first = server.ensure_auth_token("", path=str(target))
    second = server.ensure_auth_token("", path=str(target))
    assert first == second, "a service restart must not invalidate configured clients"

# ---------------------------------------------------------------------------
# TSF-01: public host allowlist for edge tunnels (Tailscale Funnel, Cloudflare)
# ---------------------------------------------------------------------------

def test_allowlisted_public_host_is_accepted(monkeypatch):
    monkeypatch.setenv("MESH_ALLOWED_HOSTS", "node.tailnet.ts.net")
    assert server.host_allowed("node.tailnet.ts.net", "127.0.0.1", 8096)
    assert server.host_allowed("node.tailnet.ts.net:443", "127.0.0.1", 8096)
    assert server.host_allowed("NODE.Tailnet.TS.net:443", "127.0.0.1", 8096)


def test_allowlist_does_not_open_foreign_hosts(monkeypatch):
    monkeypatch.setenv("MESH_ALLOWED_HOSTS", "node.tailnet.ts.net")
    assert not server.host_allowed("evil.example", "127.0.0.1", 8096)
    assert not server.host_allowed("node.tailnet.ts.net.evil.example", "127.0.0.1", 8096)
    assert not server.host_allowed("other.tailnet.ts.net", "127.0.0.1", 8096)


def test_allowlist_rejects_wildcards_ports_and_garbage(monkeypatch):
    monkeypatch.setenv("MESH_ALLOWED_HOSTS", "*, *.ts.net, node.ts.net:443, , .")
    assert server.allowed_public_hosts() == frozenset()
    assert not server.host_allowed("anything.example", "127.0.0.1", 8096)


def test_allowlist_parses_comma_separated_values(monkeypatch):
    monkeypatch.setenv("MESH_ALLOWED_HOSTS", " a.ts.net , b.example.org ")
    assert server.allowed_public_hosts() == frozenset({"a.ts.net", "b.example.org"})


def test_public_origin_requires_https_and_the_allowlist(monkeypatch):
    monkeypatch.setenv("MESH_ALLOWED_HOSTS", "node.tailnet.ts.net")
    assert server.origin_allowed("https://node.tailnet.ts.net", "127.0.0.1", 8096)
    assert server.origin_allowed("https://node.tailnet.ts.net:443", "127.0.0.1", 8096)
    assert not server.origin_allowed("http://node.tailnet.ts.net", "127.0.0.1", 8096)
    assert not server.origin_allowed("https://other.ts.net", "127.0.0.1", 8096)


def test_allowlist_is_empty_without_the_variable(monkeypatch):
    monkeypatch.delenv("MESH_ALLOWED_HOSTS", raising=False)
    assert server.allowed_public_hosts() == frozenset()
    assert not server.host_allowed("node.tailnet.ts.net", "127.0.0.1", 8096)
