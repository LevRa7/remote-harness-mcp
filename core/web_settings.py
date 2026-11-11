"""Local web settings for the node — the browser twin of the ``rhm`` TUI.

Binds loopback only. The first visit needs the token from
``<ConfigDir>/web.token``; the token is then stored in an HttpOnly cookie. Every
mutation requires that cookie plus a CSRF token, and is applied through
``core.settings`` so the web UI, the TUI and the MCP tool cannot disagree.

Run: ``python -m core.web_settings`` (or add ``--web`` to your own launcher).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import os
import secrets
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from core import control, menu, sandbox, settings, usage

MAX_BODY = 64 * 1024


def token_path() -> str:
    return os.path.join(control.control_dir(), "web.token")


def web_token() -> str:
    path = token_path()
    try:
        value = open(path, "r", encoding="utf-8").read().strip()
        if len(value) >= 24:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def csrf(token: str) -> str:
    return hmac.new(token.encode(), b"rhm-web-csrf", hashlib.sha256).hexdigest()


def _qr_data_uri(text: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg

        image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
        buffer = io.BytesIO()
        image.save(buffer)
        return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        return ""


STYLE = """
:root{color-scheme:light dark}
body{font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;max-width:960px;margin:32px auto;padding:0 18px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:24px 0 8px}
.muted{color:#777}.card{border:1px solid #8884;border-radius:14px;padding:18px;margin:12px 0}
button,.btn{display:inline-block;border:0;border-radius:9px;padding:9px 14px;background:#2563eb;color:#fff;cursor:pointer;text-decoration:none}
button.secondary{background:#666}button.danger{background:#b91c1c}
.on{outline:3px solid #16a34a}pre{background:#8881;padding:12px;border-radius:9px;overflow:auto;max-height:280px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}code{background:#8881;padding:2px 5px;border-radius:5px}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px;border-bottom:1px solid #8883}
"""


def page(token: str, message: str = "") -> str:
    snap = settings.snapshot()
    state = control.state()
    isolation = sandbox.status()
    url = menu.build_mcp_url()
    qr = _qr_data_uri(url) if url else ""
    mode = state["access_mode"]
    mode_labels = control.ACCESS_LABELS
    buttons = "".join(
        "<button name=\"mode\" value=\"%s\" class=\"%s\">%s</button>"
        % (name, "on" if name == mode else "secondary", html.escape(mode_labels[name]))
        for name in control.ACCESS_MODES
    )
    pending = control.pending_approvals()
    pending_rows = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>"
        "<form method=post><input type=hidden name=csrf value=\"%s\">"
        "<input type=hidden name=id value=\"%s\">"
        "<button name=action value=approve>Allow once</button> "
        "<button name=action value=deny class=danger>Deny</button></form></td></tr>"
        % (html.escape(item["id"]), html.escape(item["tool"]),
           html.escape(item.get("summary", "")), csrf(token), html.escape(item["id"]))
        for item in pending[-10:]
    ) or "<tr><td colspan=4 class=muted>No pending requests</td></tr>"
    isolation_text = "OFF"
    if isolation["enabled"]:
        isolation_text = "ON · %s · %s" % (isolation["workspace"], isolation["container"])
        if isolation.get("network"):
            isolation_text += " · NETWORK"
    return """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Remote Harness MCP — settings</title><style>%s</style></head><body>
<h1>Remote Harness MCP</h1><p class=muted>Node settings · local only</p>
%s
<div class=card><h2>Access</h2><p>Current: <b>%s</b></p>
<form method=post><input type=hidden name=csrf value="%s">
<div class=row>%s</div></form>
<form method=post><input type=hidden name=csrf value="%s">
<input type=hidden name=action value=pause>
<button class=secondary>%s calls</button></form></div>

<div class=card><h2>Working directory</h2><p><code>%s</code></p>
<form method=post><input type=hidden name=csrf value="%s">
<input type=hidden name=action value=cd>
<div class=row><input name=path value="%s" size=60>
<button>Set directory</button></div></form></div>

<div class=card><h2>Isolation</h2><p>%s</p>
<div class=row>
<form method=post><input type=hidden name=csrf value="%s">
<input type=hidden name=action value=sandbox><input type=hidden name=enabled value=0>
<button class=secondary>Disable</button></form>
<form method=post><input type=hidden name=csrf value="%s">
<input type=hidden name=action value=sandbox><input type=hidden name=enabled value=1>
<button>Strict (no network)</button></form>
<form method=post onsubmit="return confirm('The container will reach the host/LAN. Continue?')">
<input type=hidden name=csrf value="%s"><input type=hidden name=action value=sandbox>
<input type=hidden name=enabled value=1><input type=hidden name=network value=1>
<input type=hidden name=confirm value=yes>
<button class=danger>With network (installs)</button></form></div>
<p class=muted>Requires Docker/Podman. Only the selected project is mounted at /workspace.</p></div>

<div class=card><h2>Pending approvals</h2><table>
<tr><th>ID</th><th>Tool</th><th>Summary</th><th></th></tr>%s</table></div>

<div class=card><h2>MCP endpoint</h2>%s
%s</div>

<div class=card><h2>Agent log</h2><pre>%s</pre></div>
<p class=muted>Remote control: %s · Usage today: %s calls</p>
</body></html>""" % (
        STYLE,
        "<div class=card style='border-color:#16a34a'>%s</div>" % html.escape(message) if message else "",
        html.escape(control.ACCESS_LABELS.get(mode, mode)),
        csrf(token),
        buttons,
        csrf(token),
        "Resume" if state["paused"] else "Pause",
        html.escape(state["workspace"] or os.getcwd()),
        csrf(token),
        html.escape(state["workspace"] or os.getcwd()),
        html.escape(isolation_text),
        csrf(token), csrf(token), csrf(token),
        pending_rows,
        ("<p><code>%s</code></p>" % html.escape(url)) if url else "<p class=muted>No link key in agent.env.</p>",
        ("<img alt=QR src='%s' width=220 height=220>" % qr) if qr else "",
        html.escape("\n".join(menu.tail(menu.agent_log_path(), 14))),
        "ON" if state.get("remote_control", {}).get("enabled", True) else "OFF",
        usage.summary(days=1).get("events", 0),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "RhmWebSettings/1.0"
    token = ""
    port = 8098

    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        allowed = {"127.0.0.1:%d" % self.port, "localhost:%d" % self.port}
        if host not in allowed:
            return False
        origin = self.headers.get("Origin") or ""
        if origin:
            return origin.lower() in {"http://%s" % h for h in allowed}
        return True

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "rhm_web":
                return value
        return ""

    def _authorized(self) -> bool:
        supplied = self._cookie_token() or (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("token", [""])[0])
        return bool(supplied and hmac.compare_digest(supplied, self.token))

    def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8", headers: Optional[Dict[str, str]] = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; frame-ancestors 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed():
            self._send(403, "forbidden", "text/plain")
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/state.json":
            if not self._authorized():
                self._send(401, "unauthorized", "text/plain")
                return
            self._send(200, json.dumps(settings.snapshot(), ensure_ascii=False, default=str), "application/json")
            return
        if not self._authorized():
            self._send(401, "<h1>Token required</h1><p>Open this page with <code>?token=...</code> from web.token.</p>")
            return
        if path not in ("/", ""):
            self._send(404, "not found", "text/plain")
            return
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        message = query.get("msg", [""])[0]
        if not self._cookie_token():
            # First tokenized visit: set the cookie and drop the token from the
            # URL so it does not stay in history / Referer.
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                "rhm_web=%s; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400" % self.token,
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, page(self.token, message))

    def do_POST(self) -> None:  # noqa: N802
        if not self._allowed() or not self._authorized():
            self._send(403, "forbidden", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send(413, "body too large", "text/plain")
            return
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        data = {key: values[0] for key, values in form.items()}
        if not hmac.compare_digest(str(data.get("csrf", "")), csrf(self.token)):
            self._send(403, "bad csrf", "text/plain")
            return
        action = str(data.get("action") or "")
        message = ""
        try:
            if action in ("approve", "deny"):
                request_id = str(data.get("id") or "")
                if action == "approve":
                    result = control.approve(request_id)
                else:
                    result = {"ok": control.deny(request_id)}
                message = "approved" if action == "approve" and result else "denied"
            elif action == "cd":
                result = settings.apply({"action": "cd", "path": data.get("path", "")}, actor="tui")
                message = result.get("message") or result.get("error") or ""
            elif action == "sandbox":
                enabled = str(data.get("enabled") or "") == "1"
                network = str(data.get("network") or "") == "1"
                if network and str(data.get("confirm") or "") != "yes":
                    message = "network isolation needs confirmation"
                else:
                    result = settings.apply(
                        {"action": "sandbox", "enabled": enabled,
                         "workspace": control.workspace() or os.getcwd(),
                         "network": network}, actor="tui")
                    message = result.get("message") or result.get("error") or ""
            elif action == "pause":
                result = settings.apply({"action": "pause" if not control.paused() else "resume"}, actor="tui")
                message = result.get("message") or result.get("error") or ""
            elif action == "mode":
                result = settings.apply({"action": "mode", "mode": data.get("mode", "")}, actor="tui")
                message = result.get("message") or result.get("error") or ""
            else:
                message = "unknown action"
        except Exception as exc:
            message = "%s: %s" % (type(exc).__name__, exc)
        location = "/?msg=" + urllib.parse.quote(message)
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Never log the tokenized URL.
        sys.stderr.write("rhm-web: " + (fmt % args) + "\n")


def serve(host: str = "127.0.0.1", port: int = 8098) -> None:
    token = web_token()
    Handler.token = token
    Handler.port = int(port)
    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    httpd.daemon_threads = True
    print("rhm web settings: http://127.0.0.1:%d/?token=%s" % (port, token))
    print("token file: %s" % token_path())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="rhm-web", description="Remote Harness MCP web settings")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8098)
    args = parser.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
