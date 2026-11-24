"""The standalone (Funnel) server serves this node's shares itself.

A node installed with ``-Funnel`` is reached directly at
``https://<node>.<tailnet>.ts.net``: Tailscale terminates TLS and forwards to
the loopback server, and there is no gateway in that path at all. A share link
must therefore be served by :mod:`core.server` under ``/<slug>/<path>`` - it
used to be built for the gateway relay (``/<node>/<slug>/<path>`` on the
gateway domain) and answered ``404 not found``, because that gateway had no
tunnel to relay into.
"""

import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from core import domain, mcp_tools, server as standalone, web_share

NODE = "test-node"


def _configure(tmp_path, **overrides):
    settings = dict(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
        web_dir=str(tmp_path / "web"),
        mesh_user=NODE,
        max_share_bytes="",
        max_shares="",
        web_listing="",
        public_url="",
        web_share_direct=False,
    )
    settings.update(overrides)
    mcp_tools.configure(**settings)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in ("MESH_PUBLIC_URL", "AGY_PUBLIC_BASE_URL", "MESH_DOMAIN_FILE",
                "MESH_WEB_DIR", "MESH_WEB_MAX_BYTES", "MESH_WEB_MAX_SHARES",
                "MESH_WEB_LISTING", "MESH_WEB_DIRECT", "MESH_ALLOWED_HOSTS",
                "MESH_GATEWAY"):
        monkeypatch.delenv(key, raising=False)
    domain.reset()
    _configure(tmp_path)
    yield
    web_share.stop_all()
    domain.reset()
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
        web_dir="",
        mesh_user="",
        max_share_bytes="",
        max_shares="",
        web_listing="",
        public_url="",
        web_share_direct=False,
    )


def _serve(handler_cls, token="secret-token"):
    """Run the standalone handler on an ephemeral loopback port."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    httpd.daemon_threads = True
    port = int(httpd.server_address[1])
    handler_cls.bind_host = "127.0.0.1"
    handler_cls.bind_port = port
    handler_cls.auth_token = token
    handler_cls.server_mcp = None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def _request(url, method="GET"):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method=method)
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def test_a_share_is_public_on_the_node_itself(tmp_path):
    source = tmp_path / "shot.png"
    payload = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    source.write_bytes(payload)
    published = mcp_tools.call_tool("share_file", {"path": str(source)})
    assert published.get("ok") is True, published

    httpd, port = _serve(standalone.StandaloneMCPHandler)
    try:
        base = "http://127.0.0.1:%d" % port
        status, headers, body = _request("%s/%s/shot.png" % (base, published["slug"]))
        assert status == 200, body[:200]
        assert body == payload
        assert headers.get("Content-Type") == "image/png"

        head_status, head_headers, head_body = _request(
            "%s/%s/shot.png" % (base, published["slug"]), method="HEAD")
        assert head_status == 200
        assert head_body == b""
        assert head_headers.get("Content-Length") == str(len(payload))
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_unknown_share_is_an_opaque_404(tmp_path):
    httpd, port = _serve(standalone.StandaloneMCPHandler)
    try:
        base = "http://127.0.0.1:%d" % port
        status, _, body = _request(base + "/nosuchshare-0123456789abcdef/")
        assert status == 404
        assert body.strip() == b"not found"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_mcp_endpoint_still_needs_its_token(tmp_path):
    httpd, port = _serve(standalone.StandaloneMCPHandler)
    try:
        base = "http://127.0.0.1:%d" % port
        status, _, _ = _request(base + "/mcp")
        assert status == 401
        status, _, body = _request(base + "/health")
        assert status == 200
        assert b"standalone" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_funnel_mode_names_the_nodes_own_address(tmp_path, monkeypatch):
    """The installer records the Funnel name; links must use it."""
    monkeypatch.setenv("MESH_WEB_DIR", str(tmp_path / "web"))
    monkeypatch.setenv("MESH_ALLOWED_HOSTS", "node.tailnet.ts.net")
    standalone.configure_tools_from_env()

    assert mcp_tools.public_base_url() == "https://node.tailnet.ts.net"

    source = tmp_path / "report.md"
    source.write_bytes(b"hello\n")
    published = mcp_tools.call_tool("share_file", {"path": str(source)})
    assert published["url"] == ("https://node.tailnet.ts.net/%s/report.md"
                                % published["slug"])


def test_a_loopback_allowlist_entry_never_becomes_a_link(tmp_path, monkeypatch):
    """A malformed allowlist must not put 127.0.0.1 (or a wildcard) in a link."""
    monkeypatch.setenv("MESH_WEB_DIR", str(tmp_path / "web"))
    monkeypatch.setenv("MESH_ALLOWED_HOSTS", "*.evil.test,localhost,127.0.0.1")
    standalone._STANDALONE_PORT = 8096
    standalone.configure_tools_from_env()
    assert mcp_tools.public_base_url() == "http://localhost:8096"
