"""Tests for Wave 1 server, usage journal, CLI flags, and standalone OAuth 2.1 improvements.

Covers:
- Task 1.2: Windows exclusive port binding (allow_reuse_address=False + SO_EXCLUSIVEADDRUSE)
- Task 1.3: Usage journal size rotation (env-configurable) + async SimpleQueue daemon worker + flush()
- Task 4.1: CLI flags --folder, --no-shell, --quick-tunnel
- Task 4.2: Minimal standalone OAuth 2.1 (metadata, registration, authorize PKCE S256, token)
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from core import server, usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_standalone_http(auth_token: str = "test-static-token"):
    server.StandaloneMCPHandler.server_mcp = server.RemoteHarnessMCPServer()
    server.StandaloneMCPHandler.auth_token = auth_token
    server.StandaloneMCPHandler.oauth_store = server.StandaloneOAuthStore()
    server.StandaloneMCPHandler.bind_host = "127.0.0.1"
    server.StandaloneMCPHandler.bind_port = 0
    httpd = server._StandaloneHTTPServer(("127.0.0.1", 0), server.StandaloneMCPHandler)
    port = httpd.server_address[1]
    server.StandaloneMCPHandler.bind_port = port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def _http_request(port: int, method: str, path: str, headers=None, body: bytes = b""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    req_headers = {"Host": f"127.0.0.1:{port}"}
    if headers:
        req_headers.update(headers)
    conn.request(method, path, body=body, headers=req_headers)
    resp = conn.getresponse()
    raw = resp.read()
    resp_headers = dict(resp.getheaders())
    status = resp.status
    conn.close()
    return status, resp_headers, raw


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Task 1.2: Windows exclusive port binding (SO_EXCLUSIVEADDRUSE)
# ---------------------------------------------------------------------------

def test_windows_server_disables_reuse_address_and_blocks_double_bind():
    if sys.platform == "win32":
        assert server._StandaloneHTTPServer.allow_reuse_address is False
        first = server._StandaloneHTTPServer(("127.0.0.1", 0), server.StandaloneMCPHandler)
        port = first.server_address[1]
        try:
            with pytest.raises(OSError):
                second = server._StandaloneHTTPServer(("127.0.0.1", port), server.StandaloneMCPHandler)
                second.server_close()
        finally:
            first.server_close()
    else:
        assert server._StandaloneHTTPServer.allow_reuse_address is True


def test_server_bind_sets_so_exclusiveaddruse_on_win32(monkeypatch):
    calls = []

    class _FakeSocket:
        def setsockopt(self, level, optname, value):
            calls.append((level, optname, value))

    monkeypatch.setattr(server.sys, "platform", "win32")
    if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", 0xFFFFFFFB, raising=False)
    monkeypatch.setattr(server.ThreadingHTTPServer, "server_bind", lambda self: None)

    dummy = object.__new__(server._StandaloneHTTPServer)
    dummy.allow_reuse_address = True
    dummy.socket = _FakeSocket()
    dummy.server_bind()

    assert dummy.allow_reuse_address is False
    assert (socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1) in calls


# ---------------------------------------------------------------------------
# Task 1.3: Usage journal rotation + async SimpleQueue + daemon worker + flush
# ---------------------------------------------------------------------------

def test_usage_defaults_and_env_configuration(monkeypatch):
    assert usage.MAX_BYTES == 1 * 1024 * 1024
    assert usage.KEEP_GENERATIONS == 3

    monkeypatch.setenv("MESH_USAGE_MAX_BYTES", "2048")
    monkeypatch.setenv("MESH_USAGE_BACKUPS", "2")
    assert usage._max_bytes() == 2048
    assert usage._keep_generations() == 2


def test_usage_async_queue_worker_and_flush_preserves_all_events(tmp_path, monkeypatch):
    log_file = tmp_path / "async_usage.jsonl"
    monkeypatch.setenv("MESH_USAGE_FILE", str(log_file))
    monkeypatch.setenv("MESH_USAGE_LOG", "1")
    monkeypatch.setenv("MESH_USAGE_MAX_BYTES", str(1024 * 1024))

    assert isinstance(usage._QUEUE, queue.SimpleQueue)

    total = 50
    for i in range(total):
        usage.record("read_file", "ok", i, call_id=f"call-{i}")

    assert usage._WORKER is not None
    assert usage._WORKER.daemon is True
    assert usage.flush(timeout=5.0) is True

    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == total
    assert {row["call_id"] for row in lines} == {f"call-{i}" for i in range(total)}


def test_usage_rotation_respects_backup_limit(tmp_path, monkeypatch):
    log_file = tmp_path / "rot_usage.jsonl"
    monkeypatch.setenv("MESH_USAGE_FILE", str(log_file))
    monkeypatch.setenv("MESH_USAGE_LOG", "1")
    monkeypatch.setenv("MESH_USAGE_MAX_BYTES", "1024")
    monkeypatch.setenv("MESH_USAGE_BACKUPS", "2")

    for i in range(120):
        usage.record("grep_search", "ok", i, call_id=f"rot-{i}")
    assert usage.flush(timeout=5.0) is True

    assert os.path.exists(str(log_file) + ".1.gz")
    assert os.path.exists(str(log_file) + ".2.gz")
    assert not os.path.exists(str(log_file) + ".3.gz")

    removed = usage.clear()
    assert removed >= 2
    assert not os.path.exists(str(log_file))
    assert not os.path.exists(str(log_file) + ".1.gz")
    assert not os.path.exists(str(log_file) + ".2.gz")


# ---------------------------------------------------------------------------
# Task 4.1: CLI flags --folder, --no-shell, --quick-tunnel
# ---------------------------------------------------------------------------

def test_cli_flags_folder_and_no_shell(tmp_path, monkeypatch):
    target_dir = tmp_path / "project"
    target_dir.mkdir()
    for key in ("MESH_WORKSPACE", "MESH_WRITE_ROOTS", "MESH_PROTECT_GIT", "MESH_REMOTE_CONTROL", "MESH_ENABLE_SHELL"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)

    parser = server.build_arg_parser()
    args = parser.parse_args(["--folder", str(target_dir), "--no-shell", "--quick-tunnel"])
    assert args.folder == str(target_dir)
    assert args.no_shell is True
    assert args.quick_tunnel is True

    server.apply_cli_flags(args)
    expected = os.path.realpath(str(target_dir))
    assert os.environ["MESH_WORKSPACE"] == expected
    assert os.environ["MESH_WRITE_ROOTS"] == expected
    assert os.environ["MESH_PROTECT_GIT"] == "1"
    assert os.environ["MESH_REMOTE_CONTROL"] == "0"
    assert os.environ["MESH_ENABLE_SHELL"] == "0"
    assert server._workspace_dir() == expected


def test_cli_folder_nonexistent_returns_error(tmp_path):
    missing = tmp_path / "does-not-exist"
    rc = server.main(["--folder", str(missing)])
    assert rc == 2


def test_no_shell_blocks_shell_tools_via_jsonrpc(monkeypatch):
    monkeypatch.setenv("MESH_ENABLE_SHELL", "0")
    mcp = server.RemoteHarnessMCPServer()
    for tool_name, arguments in (
        ("bash_exec", {"command": "echo hi"}),
        ("run_script", {"code": "print(1)"}),
        ("run_job", {"command": "echo hi"}),
    ):
        status, resp = mcp.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
        assert status == 200
        assert resp is not None
        assert resp["result"]["isError"] is True
        text = resp["result"]["content"][0]["text"]
        assert "shell_disabled" in text


def test_quick_tunnel_missing_cloudflared_returns_helpful_hint(monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda _name: None)
    proc, url, msg = server.start_quick_tunnel(8096, "tok123")
    assert proc is None
    assert url is None
    assert "cloudflared" in msg.lower()
    assert "winget" in msg or "brew" in msg


def test_quick_tunnel_parses_url_and_updates_allowlist(monkeypatch):
    for key in ("MESH_ALLOWED_HOSTS", "MESH_PUBLIC_URL"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)

    class _FakeProc:
        def __init__(self):
            self.stdout = io.StringIO(
                "2026-11-26 INF +------------------------------------------------------------+\n"
                "2026-11-26 INF |  https://bright-fox-test.trycloudflare.com                 |\n"
                "2026-11-26 INF +------------------------------------------------------------+\n"
            )
            self.terminated = False

        def terminate(self):
            self.terminated = True

    fake_proc = _FakeProc()
    monkeypatch.setattr(server.shutil, "which", lambda _name: "C:/bin/cloudflared.exe")
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **kw: fake_proc)

    proc, url, msg = server.start_quick_tunnel(8096, "my-secret-tok", wait_timeout=2.0)
    assert proc is fake_proc
    assert url == "https://bright-fox-test.trycloudflare.com"
    assert "bright-fox-test.trycloudflare.com" in server.allowed_public_hosts()
    assert os.environ["MESH_PUBLIC_URL"] == "https://bright-fox-test.trycloudflare.com"
    assert "https://bright-fox-test.trycloudflare.com/mcp?token=my-secret-tok" in msg


# ---------------------------------------------------------------------------
# Task 4.2: Minimal OAuth 2.1 in standalone mode
# ---------------------------------------------------------------------------

def test_oauth_full_pkce_s256_flow_and_mcp_access():
    httpd, port = _start_standalone_http(auth_token="static-tok-123")
    try:
        # 1. Discovery metadata
        status, _hdrs, raw = _http_request(port, "GET", "/.well-known/oauth-authorization-server")
        assert status == 200
        meta = json.loads(raw.decode("utf-8"))
        assert meta["issuer"] == f"http://127.0.0.1:{port}"
        assert meta["authorization_endpoint"] == f"http://127.0.0.1:{port}/oauth/authorize"
        assert meta["token_endpoint"] == f"http://127.0.0.1:{port}/oauth/token"
        assert meta["registration_endpoint"] == f"http://127.0.0.1:{port}/oauth/register"
        assert "S256" in meta["code_challenge_methods_supported"]

        # Protected resource metadata
        status_pr, _hdrs_pr, raw_pr = _http_request(port, "GET", "/.well-known/oauth-protected-resource")
        assert status_pr == 200
        pr_meta = json.loads(raw_pr.decode("utf-8"))
        assert pr_meta["authorization_servers"] == [f"http://127.0.0.1:{port}"]

        # 2. Dynamic client registration
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        reg_body = json.dumps(
            {"client_name": "Test Web Client", "redirect_uris": [redirect_uri]}
        ).encode("utf-8")
        status, _hdrs, raw = _http_request(
            port,
            "POST",
            "/oauth/register",
            headers={"Content-Type": "application/json"},
            body=reg_body,
        )
        assert status == 201
        client = json.loads(raw.decode("utf-8"))
        client_id = client["client_id"]
        assert client_id.startswith("rhm_")

        # 3. Authorize with PKCE S256
        verifier, challenge = _pkce_pair()
        auth_query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "xyz-state-42",
                "scope": "mcp",
            }
        )
        status, hdrs, _raw = _http_request(port, "GET", f"/oauth/authorize?{auth_query}")
        assert status == 302
        location = hdrs.get("Location") or hdrs.get("location")
        assert location is not None
        loc_parts = urlsplit(location)
        assert f"{loc_parts.scheme}://{loc_parts.netloc}{loc_parts.path}" == redirect_uri
        loc_qs = parse_qs(loc_parts.query)
        code = loc_qs["code"][0]
        assert loc_qs["state"][0] == "xyz-state-42"

        # 4. Exchange code + code_verifier for bearer token (form-urlencoded)
        token_form = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
            }
        ).encode("utf-8")
        status, _hdrs, raw = _http_request(
            port,
            "POST",
            "/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=token_form,
        )
        assert status == 200
        tok_resp = json.loads(raw.decode("utf-8"))
        access_token = tok_resp["access_token"]
        assert tok_resp["token_type"] == "Bearer"
        assert tok_resp["expires_in"] >= 60

        # 5. Replaying the same authorization code must fail
        status_replay, _hdrs_r, raw_r = _http_request(
            port,
            "POST",
            "/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=token_form,
        )
        assert status_replay == 400
        assert json.loads(raw_r.decode("utf-8"))["error"] == "invalid_grant"

        # 6. Call /mcp using the OAuth Bearer token
        ping_body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode("utf-8")
        status_mcp, _hdrs_m, raw_m = _http_request(
            port,
            "POST",
            "/mcp",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            body=ping_body,
        )
        assert status_mcp == 200
        assert json.loads(raw_m.decode("utf-8"))["id"] == 7

        # 7. Existing static MESH_TOKEN continues to work alongside OAuth
        status_static, _hdrs_s, _raw_s = _http_request(
            port,
            "POST",
            "/mcp",
            headers={
                "Authorization": "Bearer static-tok-123",
                "Content-Type": "application/json",
            },
            body=ping_body,
        )
        assert status_static == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_oauth_rejects_plain_pkce_and_wrong_verifier():
    httpd, port = _start_standalone_http()
    try:
        redirect_uri = "https://example.com/callback"
        status, _hdrs, raw = _http_request(
            port,
            "POST",
            "/oauth/register",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"redirect_uris": [redirect_uri]}).encode("utf-8"),
        )
        assert status == 201
        client_id = json.loads(raw.decode("utf-8"))["client_id"]

        # Reject plain PKCE method
        bad_auth = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": "a" * 48,
                "code_challenge_method": "plain",
            }
        )
        status, _hdrs, raw = _http_request(port, "GET", f"/oauth/authorize?{bad_auth}")
        assert status == 400
        assert json.loads(raw.decode("utf-8"))["error"] == "invalid_request"

        # Issue valid code, then try exchanging with wrong verifier
        verifier, challenge = _pkce_pair()
        good_auth = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        status, hdrs, _raw = _http_request(port, "GET", f"/oauth/authorize?{good_auth}")
        assert status == 302
        location = hdrs.get("Location") or hdrs.get("location")
        code = parse_qs(urlsplit(location).query)["code"][0]

        wrong_verifier = "x" * 64
        assert wrong_verifier != verifier
        token_form = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": wrong_verifier,
            }
        ).encode("utf-8")
        status, _hdrs, raw = _http_request(
            port,
            "POST",
            "/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=token_form,
        )
        assert status == 400
        assert json.loads(raw.decode("utf-8"))["error"] == "invalid_grant"
    finally:
        httpd.shutdown()
        httpd.server_close()
