"""Local web settings: token auth, CSRF, mode change and isolation UI."""

import http.client
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

import pytest

from core import control, settings, web_settings


@pytest.fixture()
def web(tmp_path):
    token = "t" * 40
    web_settings.Handler.token = token
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_settings.Handler)
    port = httpd.server_address[1]
    web_settings.Handler.port = port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield token, port
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    payload = response.read().decode("utf-8", "replace")
    status = response.status
    result_headers = dict(response.getheaders())
    conn.close()
    return status, result_headers, payload


def test_token_is_required(web):
    _, port = web
    status, _, _ = _request(port, "GET", "/")
    assert status == 401


def test_tokenized_visit_sets_cookie_and_page_has_controls(web):
    token, port = web
    status, headers, _ = _request(port, "GET", "/?token=" + token)
    assert status == 303
    cookie = headers.get("Set-Cookie", "")
    assert "rhm_web=" in cookie
    status, _, body = _request(port, "GET", "/", headers={"Cookie": cookie.split(";")[0]})
    assert status == 200
    assert "Isolation" in body
    assert "Access" in body
    assert "Working directory" in body


def test_mode_change_requires_csrf(web):
    token, port = web
    cookie = "rhm_web=" + token
    status, _, _ = _request(
        port, "POST", "/",
        body=urllib.parse.urlencode({"action": "mode", "mode": "read_only"}),
        headers={"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 403


def test_mode_change_applies(web):
    token, port = web
    cookie = "rhm_web=" + token
    body = urllib.parse.urlencode({
        "action": "mode", "mode": "read_only",
        "csrf": web_settings.csrf(token),
    })
    status, _, _ = _request(
        port, "POST", "/", body=body,
        headers={"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 303
    assert control.access_mode() == "read_only"
