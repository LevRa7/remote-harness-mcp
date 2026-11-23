"""The page a human gets when they open the MCP endpoint in a browser.

The endpoint speaks SSE/JSON-RPC, so a browser opening it used to sit on a
stream that never renders anything.  A browser is not an MCP client: it wants
HTML, and the useful answer is the connection card - the two endpoint URLs and
the steps for the assistants this project supports.

The page is intentionally self-contained: no external CSS, fonts or scripts,
because it is served by a machine that may be behind a Funnel on a phone
hotspot.  Everything it prints is derived from the request, so the token in the
URL is the token in the button.
"""

from __future__ import annotations

import html
from string import Template
from typing import Iterable

#: A browser navigation, not an MCP client: these headers are set by every
#: current browser and by none of the MCP SDKs.
_FETCH_DOCUMENT = "document"
_FETCH_NAVIGATE = "navigate"


def is_browser(headers) -> bool:
    """True when the request looks like a human opening the URL in a browser.

    The Accept header alone is ambiguous (curl sends ``*/*``, some clients send
    ``text/html`` in a list), so the Fetch Metadata headers decide first.  An
    explicit ``text/event-stream`` always wins: an MCP client that happens to
    advertise HTML must still get its stream.
    """
    accept = (headers.get("Accept") or "").lower()
    if "text/event-stream" in accept:
        return False
    if (headers.get("Sec-Fetch-Dest") or "").lower() == _FETCH_DOCUMENT:
        return True
    if (headers.get("Sec-Fetch-Mode") or "").lower() == _FETCH_NAVIGATE:
        return True
    return "text/html" in accept


def _clipboard_button(target: str, label: str) -> str:
    return (
        '<button type="button" onclick="copyText(\'%s\')">%s</button>'
        % (target, html.escape(label))
    )


def render_landing(public_base: str, token: str, user: str = "") -> str:
    """Return the connection card as a standalone HTML document.

    *public_base* is the origin the browser used (``https://node.ts.net``),
    *token* the credential from the query string.  Both are escaped: the token
    is attacker-controlled input until it has been verified.
    """
    base = html.escape(public_base.rstrip("/"))
    token_html = html.escape(token)
    sse_url = html.escape(public_base.rstrip("/") + "/sse?token=" + token)
    http_url = html.escape(public_base.rstrip("/") + "/mcp?token=" + token)
    who = html.escape(user) if user else ""
    return _TEMPLATE.safe_substitute(
        base=base,
        token=token_html,
        sse=sse_url,
        http=http_url,
        user=who,
    )


_TEMPLATE = Template("""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Remote Harness MCP - подключение</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; padding: 24px; font: 16px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
       background: #0f1115; color: #e7e9ee; }
main { max-width: 860px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 8px; }
.muted { color: #98a2b3; font-size: 14px; }
.card { background: #171a21; border: 1px solid #262b36; border-radius: 10px; padding: 16px; margin: 12px 0; }
code, input { font-family: ui-monospace, Consolas, monospace; font-size: 13px; }
input { width: 100%; box-sizing: border-box; padding: 9px 10px; border-radius: 8px; border: 1px solid #2c3240;
        background: #0d1016; color: #e7e9ee; }
button { margin-top: 8px; padding: 8px 14px; border-radius: 8px; border: 1px solid #2c3240;
         background: #1f2530; color: #e7e9ee; cursor: pointer; }
button:hover { background: #27303d; }
ol, ul { padding-left: 22px; }
li { margin: 6px 0; }
a { color: #7cc4ff; }
.warn { border-color: #6b4a1f; background: #241c10; }
.tag { display: inline-block; font-size: 12px; padding: 2px 8px; border-radius: 999px;
       background: #1f2530; color: #98a2b3; margin-left: 8px; }
</style>
</head>
<body><main>
<h1>Remote Harness MCP <span class="tag">подключение</span></h1>
<p class="muted">Этот адрес - MCP-сервер на вашем компьютере. Открывать его в браузере
нужно только чтобы скопировать ссылку; работать с инструментами будет ассистент.
$user</p>

<div class="card warn">
<strong>Ссылка с токеном - это пароль.</strong>
<div class="muted">Кто её получил, может выполнять команды на этом ПК. Не публикуйте её,
не пересылайте в чаты и перевыпускайте токен, если он мог утечь.</div>
</div>

<h2>Адрес для MCP-клиента</h2>
<div class="card">
<div class="muted">Streamable HTTP (ChatGPT, Claude, большинство клиентов)</div>
<input id="http" readonly value="$http">
<button type="button" onclick="copyText('http')">Копировать</button>
</div>
<div class="card">
<div class="muted">SSE (Gemini и клиенты со старым транспортом)</div>
<input id="sse" readonly value="$sse">
<button type="button" onclick="copyText('sse')">Копировать</button>
</div>

<h2>Куда вставить</h2>
<ol>
<li><strong>Gemini</strong>: <a href="https://gemini.google.com/customize/connectors" target="_blank" rel="noreferrer">
Настройки → Коннекторы</a> → «Добавить коннектор» → вставьте <em>SSE</em>-ссылку.</li>
<li><strong>ChatGPT</strong>: <a href="https://chatgpt.com/#settings/Connectors" target="_blank" rel="noreferrer">
Настройки → Коннекторы</a> → включите режим разработчика → «Add custom connector» → вставьте
<em>Streamable HTTP</em>-ссылку.</li>
<li><strong>Claude</strong>: <a href="https://claude.ai/settings/connectors" target="_blank" rel="noreferrer">
Settings → Connectors</a> → «Add custom connector» → <em>Streamable HTTP</em>-ссылка.</li>
<li><strong>Другой клиент</strong>: укажите любой из адресов выше как MCP-сервер;
токен уже внутри ссылки, заголовки не нужны.</li>
</ol>

<h2>Проверка</h2>
<p class="muted">Если ассистент не подключается, откройте <a href="$base/health">$base/health</a>:
ответ <code>200</code> и <code>{"status":"healthy"}</code> означают, что узел жив, а проблема
в ссылке или в настройках клиента.</p>

<script>
function copyText(id) {
  var field = document.getElementById(id);
  if (!field) return;
  field.select();
  field.setSelectionRange(0, field.value.length);
  if (navigator.clipboard) { navigator.clipboard.writeText(field.value); }
  else { document.execCommand('copy'); }
}
</script>
</main></body></html>
""")
