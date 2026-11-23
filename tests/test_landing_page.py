"""The browser card: a human opening the endpoint gets instructions, not a stream.

The endpoint is an MCP transport.  Opening it in a browser used to leave the tab
on an SSE stream that never renders; the card turns that dead end into the two
URLs and the per-assistant steps.  A real client must be unaffected.
"""

import http.client
import threading
from http.server import ThreadingHTTPServer

from core import landing
from core import server


class _Headers(dict):
    def get(self, key, default=None):
        for name, value in self.items():
            if name.lower() == key.lower():
                return value
        return default


def _headers(**values):
    return _Headers(values)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_a_browser_navigation_is_recognised():
    assert landing.is_browser(_headers(Accept="text/html,application/xhtml+xml", **{"Sec-Fetch-Dest": "document"}))
    assert landing.is_browser(_headers(**{"Sec-Fetch-Mode": "navigate"}))
    assert landing.is_browser(_headers(Accept="text/html", **{"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/140"}))


def test_an_mcp_client_is_not_mistaken_for_a_browser():
    assert not landing.is_browser(_headers(Accept="text/event-stream"))
    assert not landing.is_browser(_headers(Accept="text/event-stream", **{"Sec-Fetch-Dest": "document"}))
    assert not landing.is_browser(_headers(Accept="*/*"))
    assert not landing.is_browser(_headers())
    # A validator that advertises HTML without being a browser must still get the
    # stream: this is what made Gemini answer "not an MCP server address".
    assert not landing.is_browser(_headers(Accept="text/html", **{"User-Agent": "python-requests/2.32"}))
    assert not landing.is_browser(_headers(Accept="text/html"))


# ---------------------------------------------------------------------------
# The card itself
# ---------------------------------------------------------------------------

def test_the_card_carries_both_endpoints_and_the_token():
    page = landing.render_landing("https://node.ts.net", "tok123")
    assert "https://node.ts.net/sse?token=tok123" in page
    assert "https://node.ts.net/mcp?token=tok123" in page
    assert "Gemini" in page and "ChatGPT" in page and "Claude" in page
    assert "no-referrer" in page, "the page must not leak the token as a referrer"


def test_the_card_escapes_what_came_from_the_url():
    page = landing.render_landing("https://node.ts.net", '"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# ---------------------------------------------------------------------------
# End to end through the real handler
# ---------------------------------------------------------------------------

def _start():
    server.StandaloneMCPHandler.auth_token = "test-token"
    server.StandaloneMCPHandler.bind_host = "127.0.0.1"
    server.StandaloneMCPHandler.bind_port = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.StandaloneMCPHandler)
    server.StandaloneMCPHandler.bind_port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def _get(port, path, headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    status = response.status
    content_type = response.getheader("Content-Type", "")
    body = response.read(4096) if "text/html" in content_type else b""
    conn.close()
    return status, content_type, body


def test_browser_with_a_valid_token_gets_the_card():
    httpd, port = _start()
    try:
        status, content_type, body = _get(port, "/sse?token=test-token", {
            "Accept": "text/html",
            "Sec-Fetch-Dest": "document",
        })
        assert status == 200
        assert content_type.startswith("text/html")
        assert b"ChatGPT" in body
        assert b"test-token" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_mcp_client_still_gets_the_stream():
    httpd, port = _start()
    try:
        status, content_type, _body = _get(port, "/sse?token=test-token", {
            "Accept": "text/event-stream",
        })
        assert status == 200
        assert content_type.startswith("text/event-stream")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_browser_without_a_valid_token_gets_no_card():
    httpd, port = _start()
    try:
        status, _content_type, _body = _get(port, "/sse?token=wrong", {
            "Accept": "text/html",
            "Sec-Fetch-Dest": "document",
        })
        assert status == 401, "the card carries the token: it must never render unauthenticated"
    finally:
        httpd.shutdown()
        httpd.server_close()

def test_the_stream_announces_a_post_endpoint_that_carries_the_token():
    httpd, port = _start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/sse?token=test-token", headers={"Accept": "text/event-stream"})
        response = conn.getresponse()
        first = response.read1(200)
        conn.close()
        assert b"event: endpoint" in first
        assert b"data: /messages?token=test-token" in first, (
            "the client posts to the announced endpoint; without the token that POST is 401"
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
