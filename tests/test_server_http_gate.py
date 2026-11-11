"""End-to-end gate for the standalone HTTP handler (not just the helpers).

The unit tests cover host/origin/content-type predicates; this one drives a real
``ThreadingHTTPServer`` so a future refactor cannot bypass them in the handler.
"""

import http.client
import threading

from http.server import ThreadingHTTPServer

from core import server


def _start():
    server.StandaloneMCPHandler.auth_token = "test-token"
    server.StandaloneMCPHandler.bind_host = "127.0.0.1"
    server.StandaloneMCPHandler.bind_port = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.StandaloneMCPHandler)
    server.StandaloneMCPHandler.bind_port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def _post(port, headers, body=b"{}"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/mcp", body=body, headers=headers)
    response = conn.getresponse()
    status = response.status
    response.read()
    conn.close()
    return status


def test_foreign_host_is_rejected_before_auth():
    httpd, port = _start()
    try:
        status = _post(port, {
            "Host": "evil.example:%d" % port,
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        })
        assert status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_missing_token_is_unauthorized():
    httpd, port = _start()
    try:
        status = _post(port, {
            "Host": "127.0.0.1:%d" % port,
            "Content-Type": "application/json",
        })
        assert status == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_text_plain_body_is_rejected():
    httpd, port = _start()
    try:
        status = _post(port, {
            "Host": "127.0.0.1:%d" % port,
            "Authorization": "Bearer test-token",
            "Content-Type": "text/plain",
        })
        assert status == 415
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_foreign_origin_is_rejected():
    httpd, port = _start()
    try:
        status = _post(port, {
            "Host": "127.0.0.1:%d" % port,
            "Origin": "https://evil.example",
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        })
        assert status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


class _DummyMCP:
    def handle_jsonrpc(self, body):
        return 200, {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_loopback_query_token_is_accepted_for_the_standalone_ui():
    httpd, port = _start()
    server.StandaloneMCPHandler.server_mcp = _DummyMCP()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/mcp?token=test-token",
            body=b"{}",
            headers={
                "Host": "127.0.0.1:%d" % port,
                "Content-Type": "application/json",
            },
        )
        response = conn.getresponse()
        assert response.status == 200
        response.read()
        conn.close()
    finally:
        server.StandaloneMCPHandler.server_mcp = None
        httpd.shutdown()
        httpd.server_close()
