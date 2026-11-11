"""The account cabinet app (mounted at ``/account`` by gateway.py).

Besides the login, the cabinet is where a logged-in person controls the node
they own: the remote-control lock, the access mode, the working directory, the
isolated container and pause. Every button posts to this app, which forwards one
``node_control`` call over the gateway tunnel - the same tool a model calls, with
the same validation on the node (``core/settings.py``). The cabinet never writes
a node's settings itself.
"""

from __future__ import annotations

import html
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from . import auth, drive as drive_api, sessions
from .config import AccountConfig
from .db import Store

STYLE = """
:root{color-scheme:light dark}
body{font:16px/1.5 system-ui,Segoe UI,Arial,sans-serif;max-width:820px;margin:40px auto;padding:0 20px}
.card{border:1px solid #8884;border-radius:14px;padding:22px;margin:0 0 18px}
.card h1{font-size:24px;margin:0 0 4px}
.card h2{font-size:18px;margin:0 0 10px}
h1{font-size:26px;margin:0 0 4px}
.muted{color:#777}
.btn{display:inline-block;background:#2563eb;color:#fff;padding:9px 15px;border-radius:9px;text-decoration:none;border:0;cursor:pointer;font:inherit}
.btn.secondary{background:#666}
.btn.danger{background:#b91c1c}
.btn:disabled{background:#999;cursor:not-allowed}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0}
code{background:#8881;padding:2px 6px;border-radius:6px}
table{width:100%;border-collapse:collapse;margin-top:16px}
td,th{text-align:left;padding:8px;border-bottom:1px solid #8883}
.notice{border:1px solid #16a34a;background:#16a34a22;padding:10px 14px;border-radius:10px;margin:0 0 16px}
.error{border:1px solid #dc2626;background:#dc262622;padding:10px 14px;border-radius:10px;margin:0 0 16px}
.warn{border:1px solid #d97706;background:#d9770622;padding:10px 14px;border-radius:10px}
input[type=text],input[type=password]{padding:8px 10px;border:1px solid #8886;border-radius:8px;font:inherit;min-width:280px}
.pill{display:inline-block;border-radius:999px;padding:1px 10px;font-size:13px;background:#8882}
.pill.on{background:#16a34a33}
.pill.off{background:#dc262633}
.modal{position:fixed;inset:0;background:#0007;display:flex;align-items:center;justify-content:center;padding:20px;z-index:10}
.modalbox{background:Canvas;max-width:640px;width:100%;box-shadow:0 18px 60px #0006;border-radius:14px;padding:8px 20px 20px}
.modalbox .card{border:0}
"""

CHANNEL_UNAVAILABLE = (
    "Шлюз не подключён к этому узлу: удалённое управление сейчас недоступно. "
    "Выполните действия на самой машине через <code>rhm</code>."
)

CLAIM_ERRORS = {
    "missing": "Укажите имя узла и его ключ.",
    "unknown_node": "Узел с таким именем не зарегистрирован на этом шлюзе.",
    "bad_credential": "Ключ не подходит к этому узлу.",
    "registry_unavailable": "Реестр шлюза недоступен; привязка временно запрещена.",
    "already_linked": "Этот узел уже привязан к другому аккаунту.",
    "rate_limited": "Слишком много попыток привязки. Подождите несколько минут.",
}


def _headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    document = (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s</title><style>%s</style></head><body>%s</body></html>"
        % (html.escape(title), STYLE, body)
    )
    return _headers(HTMLResponse(document, status_code=status))


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def create_app(config: Optional[AccountConfig] = None,
               node_control: Any = None) -> Starlette:
    cfg = config or AccountConfig.from_env()
    store = Store(cfg.db_path) if cfg.configured else None
    #: Per-tenant claim attempts: a code or credential is guessable only if the
    #: caller may try forever, so the cabinet slows the obvious attack down.
    claim_guard: Dict[str, List[float]] = {}

    def current(request: Request) -> Optional[Dict[str, Any]]:
        if store is None or not cfg.configured:
            return None
        sid = sessions.session_id(request, cfg.session_secret, cfg.base_url)
        return store.session(sid) if sid else None

    async def require(request: Request) -> Optional[Dict[str, Any]]:
        return current(request)

    def gateway_ready() -> bool:
        return node_control is not None and bool(getattr(node_control, "available", lambda: False)())

    def nodes_for(account: Dict[str, Any]) -> List[Dict[str, Any]]:
        if store is None:
            return []
        return store.list_devices(account["tenant_id"])

    def online(node: str) -> bool:
        try:
            return bool(node_control.online(node))
        except Exception:
            return False

    async def snapshot(node: str) -> Dict[str, Any]:
        """One ``node_control`` status call; never raises."""
        if not gateway_ready():
            return {"error": CHANNEL_UNAVAILABLE}
        try:
            result = await node_control.call(node, {"action": "status"})
        except Exception as exc:                      # a bridge failure is a page, not a 500
            return {"error": "%s: %s" % (type(exc).__name__, exc)}
        return result if isinstance(result, dict) else {"error": "invalid response"}

    async def control(node: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not gateway_ready():
            return {"error": CHANNEL_UNAVAILABLE}
        try:
            result = await node_control.call(node, payload)
        except Exception as exc:
            return {"error": "%s: %s" % (type(exc).__name__, exc)}
        return result if isinstance(result, dict) else {"error": "invalid response"}

    def back(path: str, result: Optional[Dict[str, Any]] = None) -> RedirectResponse:
        """Redirect with one line of feedback; secrets and paths stay out of it."""
        notice = ""
        error = ""
        if result:
            if result.get("error"):
                error = str(result["error"])[:300]
            else:
                notice = str(result.get("message") or "Готово.")[:300]
        target = path + "?" + "&".join(
            part for part in (
                "notice=" + quote(notice) if notice else "",
                "error=" + quote(error) if error else "",
            ) if part
        )
        return _headers(RedirectResponse(target, status_code=303))

    def feedback(request: Request) -> str:
        notice = request.query_params.get("notice") or ""
        error = request.query_params.get("error") or ""
        block = ""
        if notice:
            block += "<p class=notice>%s</p>" % _esc(notice)
        if error:
            block += "<p class=error>%s</p>" % _esc(error)
        return block

    def csrf_ok(request: Request, account: Dict[str, Any], form: Any) -> bool:
        supplied = str(form.get("csrf") or "")
        return bool(supplied) and supplied == str(account.get("csrf") or "")

    # -- rendering ---------------------------------------------------------

    def lock_controls(device: Dict[str, Any], state: Dict[str, Any],
                      csrf: str, compact: bool = False) -> str:
        """The remote-control switch, plus a warning once it is off."""
        node = str(device.get("node_name") or "")
        lock = state.get("remote_control") or {}
        enabled = lock.get("enabled")
        if enabled is None:
            return "<p class=muted>Состояние переключателя неизвестно: узел не отвечает.</p>"
        badge = ("<span class=\"pill on\">включено</span>" if enabled
                 else "<span class=\"pill off\">выключено</span>")
        head = "<div class=row><strong>Удалённое управление доступом к этому MCP</strong> %s</div>" % badge
        if enabled:
            return head + (
                "<p class=muted>Кабинет может включать read-only, менять рабочую папку, "
                "запускать изолированную среду и ставить узел на паузу. Модель может "
                "только ужесточать режим, но не снимать read-only.</p>"
                "<form method=post action=\"/account/devices/%s/control\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<input type=hidden name=action value=remote_control>"
                "<input type=hidden name=enabled value=false>"
                "<button class=\"btn danger\" type=submit>Выключить удалённое управление</button>"
                "</form>"
                "<p class=muted>После выключения включить его обратно можно будет только "
                "на самой машине: <code>rhm remote-control on</code>.</p>"
                % (_esc(device.get("id")), _esc(csrf))
            )
        return head + (
            "<p class=warn>Удалённое управление выключено. Включить его снова можно "
            "только на самой машине: <code>rhm remote-control on</code> "
            "(или клавишей <code>r</code> в консольном меню <code>rhm</code>).</p>"
        )

    def device_settings(device: Dict[str, Any], state: Dict[str, Any], csrf: str) -> str:
        node = _esc(device.get("node_name"))
        device_id = _esc(device.get("id"))
        lock = (state.get("remote_control") or {}).get("enabled")
        if lock is False:
            return "<p class=warn>Настройки недоступны: удалённое управление выключено " \
                   "на этом узле.</p>"

        mode = state.get("access_mode") or "?"
        buttons = []
        for value, label in (("read_only", "Read-only"),
                             ("approval", "С подтверждением"),
                             ("full", "Полный доступ")):
            buttons.append(
                "<button class=\"btn%s\" type=submit name=mode value=\"%s\">%s</button>"
                % ("" if value != mode else " secondary", value, label)
            )
        mode_form = (
            "<form method=post action=\"/account/devices/%s/control\">"
            "<input type=hidden name=csrf value=\"%s\">"
            "<input type=hidden name=action value=mode>"
            "<div class=row>%s</div></form>"
            % (device_id, _esc(csrf), "".join(buttons))
        )

        workspace = state.get("workspace") or ""
        cd_form = (
            "<form method=post action=\"/account/devices/%s/control\">"
            "<input type=hidden name=csrf value=\"%s\">"
            "<input type=hidden name=action value=cd>"
            "<div class=row><input type=text name=path value=\"%s\" placeholder=\"%s\">"
            "<button class=btn type=submit>Сменить папку</button></div></form>"
            % (device_id, _esc(csrf), _esc(workspace), "D:\\\\work")
        )

        isolation = state.get("sandbox") or {}
        if isolation.get("enabled"):
            isolation_form = (
                "<form method=post action=\"/account/devices/%s/control\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<input type=hidden name=action value=sandbox>"
                "<input type=hidden name=enabled value=false>"
                "<button class=\"btn secondary\" type=submit>Выключить изоляцию</button>"
                "</form><p class=muted>Контейнер: <code>%s</code> · <code>%s</code></p>"
                % (device_id, _esc(csrf), _esc(isolation.get("container")),
                   _esc(isolation.get("workspace")))
            )
        else:
            isolation_form = (
                "<form method=post action=\"/account/devices/%s/control\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<input type=hidden name=action value=sandbox>"
                "<input type=hidden name=enabled value=true>"
                "<div class=row><input type=text name=workspace value=\"%s\" "
                "placeholder=\"Папка проекта\"></div>"
                "<div class=row><label><input type=checkbox name=network value=true%s> "
                "разрешить сеть внутри контейнера</label></div>"
                "<div class=row><button class=btn type=submit>"
                "Запустить изолированную среду</button></div></form>"
                "<p class=muted>Docker/Podman, в контейнер монтируется только папка проекта "
                "(<code>/workspace</code>), без доступа к остальным путям хоста. Сеть по "
                "умолчанию выключена; она нужна только для установки пакетов внутри "
                "контейнера.</p>"
                % (device_id, _esc(csrf), _esc(workspace),
                   " checked" if isolation.get("network") else "")
            )

        paused = bool(state.get("paused"))
        pause_action = "resume" if paused else "pause"
        pause_label = "Снять паузу" if paused else "Поставить на паузу"
        pause_form = (
            "<form method=post action=\"/account/devices/%s/control\">"
            "<input type=hidden name=csrf value=\"%s\">"
            "<input type=hidden name=action value=%s>"
            "<button class=\"btn secondary\" type=submit>%s</button></form>"
            % (device_id, _esc(csrf), pause_action, pause_label)
        )

        return (
            "<p>Текущий режим: <strong>%s</strong>%s</p>"
            "<h2>Режим доступа</h2>%s"
            "<h2>Рабочая папка</h2>%s"
            "<h2>Изолированная среда</h2>%s"
            "<h2>Активность</h2>%s"
            % (_esc(mode), " · на паузе" if paused else "", mode_form, cd_form,
               isolation_form, pause_form)
        )

    # -- routes ------------------------------------------------------------

    async def root(request: Request) -> Response:
        if not cfg.configured:
            return page(
                "Remote Harness MCP",
                "<div class=card><h1>Кабинет не настроен</h1>"
                "<p class=muted>Задайте <code>MESH_ACCOUNT=1</code>, "
                "<code>MESH_GOOGLE_CLIENT_ID</code>, <code>MESH_GOOGLE_CLIENT_SECRET</code>, "
                "<code>MESH_SESSION_SECRET</code> и перезапустите шлюз. MCP-транспорт работает.</p></div>",
                status=200,
            )
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        devices = nodes_for(account)
        csrf = str(account.get("csrf") or "")

        rows = []
        for device in devices:
            node = str(device.get("node_name") or "")
            state = await snapshot(node)
            lock = (state.get("remote_control") or {}).get("enabled")
            lock_label = ("—" if lock is None else ("включено" if lock else "выключено"))
            rows.append(
                "<tr><td><a href=\"/account/devices/%s\">%s</a></td>"
                "<td>%s</td><td>%s</td><td>%s</td></tr>"
                % (_esc(device.get("id")), _esc(node),
                   "онлайн" if online(node) else "офлайн",
                   _esc(state.get("access_mode") or "—"), lock_label)
            )

        device_table = (
            "<table><tr><th>Устройство</th><th>Связь</th><th>Режим</th>"
            "<th>Удалённое управление</th></tr>%s</table>" % "".join(rows)
            if rows else
            "<p class=muted>Устройства ещё не привязаны.</p>"
        )

        # The setting the product asks about right after login: it is shown on
        # the dashboard, not buried in a submenu.
        if devices:
            primary = devices[0]
            state = await snapshot(str(primary.get("node_name") or ""))
            lock_card = (
                "<div class=card><h2>Удалённое управление MCP</h2>%s"
                "<p class=muted>Это тот же переключатель, что и "
                "<code>rhm remote-control</code> на самой машине.</p></div>"
                % lock_controls(primary, state, csrf)
            )
        else:
            lock_card = (
                "<div class=card><h2>Привяжите устройство</h2>"
                "<p class=muted>Введите имя узла и его ключ (ссылка MCP содержит "
                "<code>lk_...</code>; ключ устройства также принимается). Ключ нужен один "
                "раз, чтобы доказать, что устройство ваше.</p>"
                "<form method=post action=\"/account/devices/claim\">"
                "<input type=hidden name=csrf value=\"%s\">"
                "<div class=row><input type=text name=node placeholder=\"имя узла\"></div>"
                "<div class=row><input type=password name=credential placeholder=\"lk_... или токен устройства\">"
                "<button class=btn type=submit>Привязать</button></div></form></div>"
                % _esc(csrf)
            )

        account_card = (
            "<div class=card><h1>Remote Harness MCP</h1>"
            "<p class=muted>%s · аккаунт %s</p>"
            "<p>Устройств: %d</p>"
            "<form method=post action=/account/logout>"
            "<input type=hidden name=csrf value=\"%s\">"
            "<button class=\"btn secondary\" type=submit>Выйти</button></form></div>"
            % (_esc(account.get("email") or ""),
               _esc(str(account.get("id") or "")[:12]), len(devices), _esc(csrf))
        )
        devices_card = "<div class=card><h2>Устройства</h2>%s</div>" % device_table
        drive_card = (
            "<div class=card><h2>Google Drive</h2>%s</div>"
            % ("<p>Подключён · <a href=\"/account/drive\">открыть файлы</a></p>"
               if store.google_tokens(account["id"]) else
               "<p class=muted>Не подключён.</p>"
               "<a class=btn href=\"/account/drive/connect\">Подключить Drive</a>")
        )
        body = feedback(request) + lock_card + account_card + drive_card + devices_card
        if request.query_params.get("welcome") == "1":
            # The setting the product asks about right after the login: a modal
            # over the dashboard, with the same controls as the page below.
            body = (
                "<div class=modal><div class=modalbox>%s"
                "<div class=row><a class=btn href=\"/account\">Понятно</a></div>"
                "</div></div>"
                % lock_card
            ) + body
        return page("Remote Harness MCP", body)

    async def device_page(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        if store is None:
            return page("Кабинет", "<div class=card><h1>Кабинет не настроен</h1></div>")
        device = store.device(str(request.path_params.get("device_id") or ""),
                              account["tenant_id"])
        if device is None:
            return page("Не найдено", "<div class=card><h1>Устройство не найдено</h1>"
                                      "<p><a href=\"/account\">В кабинет</a></p></div>", status=404)
        node = str(device.get("node_name") or "")
        state = await snapshot(node)
        body = (
            "%s<div class=card><h1>%s</h1>"
            "<p class=muted>%s</p>%s"
            "<p class=muted>Ссылка MCP с ключом узла остаётся на самой машине: "
            "<code>rhm --once</code> показывает её и QR-код.</p>"
            "<p><a class=\"btn secondary\" href=\"/account\">В кабинет</a> "
            "<a class=\"btn secondary\" href=\"/account/devices/%s\">Обновить</a></p></div>"
            "<div class=card>%s</div>"
            % (
                feedback(request), _esc(node),
                "онлайн" if online(node) else "офлайн (изменения не пройдут, пока агент не подключится)",
                "" if not state.get("error") else "<p class=error>%s</p>" % _esc(state["error"]),
                _esc(device.get("id")),
                "%s<hr><h2>Настройки</h2>%s"
                % (lock_controls(device, state, str(account.get("csrf") or "")),
                   device_settings(device, state, str(account.get("csrf") or ""))),
            )
        )
        return page("Настройки — %s" % node, body)

    async def claim(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        if request.method != "POST" or store is None:
            return _headers(Response("Use POST", status_code=405))
        form = await request.form()
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        tenant = account["tenant_id"]
        now = time.time()
        attempts = [stamp for stamp in claim_guard.get(tenant, []) if now - stamp < 600]
        if len(attempts) >= 10:
            return back("/account", {"error": CLAIM_ERRORS["rate_limited"]})
        attempts.append(now)
        claim_guard[tenant] = attempts

        node = str(form.get("node") or "").strip().lower()
        credential = str(form.get("credential") or "").strip()
        if not gateway_ready():
            return back("/account", {"error": "Шлюз не подключён: привязка недоступна."})
        try:
            verified = node_control.claim(node, credential)
        except Exception as exc:
            return back("/account", {"error": "%s: %s" % (type(exc).__name__, exc)})
        if not verified.get("ok"):
            return back("/account", {"error": CLAIM_ERRORS.get(
                str(verified.get("error") or ""), "Не удалось привязать устройство.")})
        device = store.claim_device(tenant, node)
        if device is None:
            return back("/account", {"error": CLAIM_ERRORS["already_linked"]})
        return back("/account/devices/%s" % device["id"],
                    {"message": "Устройство %s привязано." % node})

    async def device_control(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account", status_code=303)
        if request.method != "POST" or store is None:
            return _headers(Response("Use POST", status_code=405))
        form = await request.form()
        device = store.device(str(request.path_params.get("device_id") or ""),
                              account["tenant_id"])
        if device is None:
            return page("Не найдено", "<div class=card><h1>Устройство не найдено</h1></div>", 404)
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        payload: Dict[str, Any] = {"action": str(form.get("action") or "status")}
        if form.get("mode"):
            payload["mode"] = str(form.get("mode"))
        if form.get("path"):
            payload["path"] = str(form.get("path"))
        if form.get("workspace"):
            payload["workspace"] = str(form.get("workspace"))
        if form.get("enabled") is not None and str(form.get("enabled")) != "":
            payload["enabled"] = _is_true(form.get("enabled"))
        if payload["action"] == "sandbox" and payload.get("enabled"):
            payload["network"] = _is_true(form.get("network"))
        node = str(device.get("node_name") or "")
        result = await control(node, payload)
        if not result.get("error"):
            store.touch_device(device["id"])
        return back("/account/devices/%s" % device["id"], result)

    async def drive_page(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account/drive", status_code=303)
        record = store.google_tokens(account["id"])
        files: list = []
        problem = ""
        if record:
            try:
                token = drive_api.access_token(cfg, store, account["id"])
                files = drive_api.list_files(token)
            except Exception as exc:
                problem = str(exc)
        if record and not problem:
            body = (
                "<div class=card><h1>Google Drive</h1>"
                "<p class=muted>Подключён · scope <code>drive.file</code> · файлов: %d</p>"
                "<table><tr><th>Файл</th><th>Тип</th><th>Изменён</th></tr>%s</table>"
                "<form method=post action=/account/drive/disconnect>"
                "<input type=hidden name=csrf value=\"%s\">"
                "<button class=\"btn secondary\" type=submit>Отключить Drive</button></form>"
                "<p class=muted>Агент сможет читать/загружать только эти файлы; "
                "остальной Drive недоступен.</p></div>"
                % (len(files), "".join(
                    "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (_esc(item.get("name")), _esc(item.get("mimeType")),
                       _esc(item.get("modifiedTime")))
                    for item in files
                ) or "<tr><td colspan=3 class=muted>Пока нет файлов, созданных приложением.</td></tr>",
                   _esc(account.get("csrf")))
            )
        else:
            body = (
                "<div class=card><h1>Google Drive</h1>"
                "<p class=muted>%s</p>"
                "<p>Нужен доступ <code>drive.file</code> — только файлы, созданные/открытые "
                "этим приложением. Весь Drive не открывается.</p>"
                "<a class=btn href=/account/drive/connect>Подключить Google Drive</a>"
                "<p class=muted>Токен хранится зашифрованным на шлюзе, отдельно для каждого "
                "аккаунта.</p></div>"
                % (_esc(problem or "Drive ещё не подключён."))
            )
        return page("Google Drive — Remote Harness MCP", body)

    async def drive_start(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login?next=/account/drive", status_code=303)
        state = auth.new_state()
        nonce = auth.new_nonce()
        verifier = auth.new_verifier()
        store.create_flow(state, nonce, verifier, "/account/drive",
                          kind="drive", account_id=account["id"])
        return RedirectResponse(
            auth.build_auth_url(cfg, state, nonce, verifier, scopes=auth.DRIVE_SCOPES,
                                access_type="offline", prompt="consent"),
            status_code=302,
        )

    async def drive_disconnect(request: Request) -> Response:
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login", status_code=303)
        form = await request.form()
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        store.delete_google_tokens(account["id"])
        return RedirectResponse("/account/drive?disconnected=1", status_code=303)

    async def login(request: Request) -> Response:
        if not cfg.configured:
            return RedirectResponse("/account", status_code=303)
        if await require(request) is not None:
            return RedirectResponse("/account", status_code=303)
        nxt = auth.sanitize_next(request.query_params.get("next"))
        body = (
            "<div class=card><h1>Вход в кабинет</h1>"
            "<p class=muted>Войдите через Google, чтобы управлять своими устройствами.</p>"
            "<a class=btn href=\"/account/auth/google/start?next=%s\">Войти через Google</a>"
            "<p class=muted>Ссылка MCP не публикуется. Кабинет хранит только метаданные.</p></div>"
            % html.escape(nxt, quote=True)
        )
        return page("Вход — Remote Harness MCP", body)

    async def start(request: Request) -> Response:
        if not cfg.configured:
            return page("Не настроено", "<div class=card><h1>OAuth не настроен</h1></div>", 503)
        state = auth.new_state()
        nonce = auth.new_nonce()
        verifier = auth.new_verifier()
        store.create_flow(state, nonce, verifier, auth.sanitize_next(request.query_params.get("next")))
        return RedirectResponse(auth.build_auth_url(cfg, state, nonce, verifier), status_code=302)

    async def callback(request: Request) -> Response:
        if not cfg.configured:
            return page("Не настроено", "<div class=card><h1>OAuth не настроен</h1></div>", 503)
        error = request.query_params.get("error")
        state = request.query_params.get("state", "")
        if error:
            return page("Вход отменён", "<div class=card><h1>Вход отменён</h1><p>%s</p></div>"
                        % html.escape(error), 400)
        flow = store.consume_flow(state)
        if flow is None:
            return page("Вход отклонён", "<div class=card><h1>Сессия входа истекла</h1>"
                        "<p class=muted>Повторите вход.</p><a class=btn href=\"/account/login\">Войти</a></div>", 400)
        code = request.query_params.get("code", "")
        if not code:
            return page("Вход отклонён", "<div class=card><h1>Нет кода авторизации</h1></div>", 400)
        if str(flow.get("kind") or "login") == "drive":
            account = await require(request)
            if account is None or str(account.get("id")) != str(flow.get("account_id")):
                return page("Drive отклонён", "<div class=card><h1>Сессия не совпадает</h1>"
                            "<p class=muted>Начните подключение Drive заново из кабинета.</p></div>", 403)
            try:
                tokens = auth.exchange_code(cfg, code, flow["verifier"])
                claims, problem = auth.verify_id_token(cfg, tokens.get("id_token", ""), flow["nonce"])
                if claims is None:
                    raise RuntimeError(problem)
                drive_api.save_tokens(cfg, store, account["id"], tokens)
            except Exception as exc:
                return page("Drive не подключён", "<div class=card><h1>Не удалось подключить Drive</h1>"
                            "<p class=muted>%s</p><a class=btn href=\"/account/drive\">Назад</a></div>"
                            % html.escape(str(exc)), 400)
            return RedirectResponse("/account/drive?connected=1", status_code=303)
        try:
            tokens = auth.exchange_code(cfg, code, flow["verifier"])
            claims, problem = auth.verify_id_token(cfg, tokens.get("id_token", ""), flow["nonce"])
        except Exception as exc:
            return page("Ошибка входа", "<div class=card><h1>Google недоступен</h1>"
                        "<p class=muted>%s</p></div>" % html.escape(str(exc)), 502)
        if claims is None:
            return page("Ошибка входа", "<div class=card><h1>Токен отклонён</h1>"
                        "<p class=muted>%s</p></div>" % html.escape(problem), 400)
        account = store.upsert_account(claims)
        session = store.create_session(account["id"])
        target = flow.get("next_path") or "/account"
        if target == "/account":
            # Right after the login, ask the one question the product cares
            # about: is this MCP controllable remotely, and by whom.
            target = "/account?welcome=1"
        response = RedirectResponse(target, status_code=303)
        sessions.set_cookie(response, session["id"], cfg.session_secret, cfg.base_url)
        return _headers(response)

    async def logout(request: Request) -> Response:
        if request.method == "GET":
            return _headers(Response("Use POST", status_code=405))
        account = await require(request)
        if account is None:
            return RedirectResponse("/account/login", status_code=303)
        form = await request.form()
        if not csrf_ok(request, account, form):
            return page("Ошибка", "<div class=card><h1>CSRF-токен неверен</h1></div>", 403)
        sid = sessions.session_id(request, cfg.session_secret, cfg.base_url)
        if sid:
            store.revoke_session(sid)
        response = RedirectResponse("/account/login", status_code=303)
        sessions.clear_cookie(response, cfg.base_url)
        return _headers(response)

    app = Starlette(routes=[
        Route("/", root, methods=["GET", "HEAD"]),
        Route("/devices/claim", claim, methods=["POST"]),
        Route("/devices/{device_id}", device_page, methods=["GET", "HEAD"]),
        Route("/devices/{device_id}/control", device_control, methods=["POST"]),
        Route("/drive", drive_page, methods=["GET", "HEAD"]),
        Route("/drive/connect", drive_start, methods=["GET"]),
        Route("/drive/disconnect", drive_disconnect, methods=["POST"]),
        Route("/login", login, methods=["GET", "HEAD"]),
        Route("/auth/google/start", start, methods=["GET"]),
        Route("/auth/google/callback", callback, methods=["GET"]),
        Route("/logout", logout, methods=["GET", "POST"]),
    ])
    app.state.account_config = cfg
    app.state.account_store = store
    app.state.node_control = node_control
    return app
