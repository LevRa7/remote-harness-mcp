# Веб-настройки узла (`rhm --web`)

Локальная веб-версия TUI: те же переключатели, что в `rhm`, но в браузере.

```bash
python -m core.web_settings        # http://127.0.0.1:8098
rhm --web                          # то же через меню-лаунчер
```

При старте печатается URL с одноразовым токеном:

```
http://127.0.0.1:8098/?token=<...>
```

Токен хранится в `<ConfigDir>/web.token` (0600). После первого визита он
переезжает в HttpOnly-cookie, а из адресной строки исчезает.

## Что на странице

- **Access** — Read-only / Access on request / Full access (те же правила, что
  в TUI; явный выбор перекрывает `MESH_READ_ONLY`, env — fallback).
- **Working directory** — путь проекта.
- **Isolation** — Disable / Strict (network none) / With network (кнопка с
  подтверждением; нужна для установки пакетов внутри контейнера).
- **Pending approvals** — Allow once / Deny.
- **MCP endpoint** — ссылка и QR.
- **Agent log** — последние строки.
- Pause/Resume, remote-control status, usage today.

## Безопасность

- Только loopback; `Host`/`Origin` проверяются.
- Токен + HttpOnly cookie; все POST требуют CSRF.
- CSP без inline-script, `frame-ancestors 'none'`, `no-store`.
- Мутации идут через `core.settings.apply`, локальный актор `tui`; сетевой
  sandbox требует явного `confirm=yes`.
- Не публикуйте порт 8098 наружу. Для удалённого доступа — SSH-туннель:
  `ssh -L 8098:127.0.0.1:8098 root@host`.

## Ограничения

- Нет нативного выбора папки (браузер не даёт путь) — вводится текстом.
- Нет live-обновления: страница перерисовывается после действия; лог
  обновляется при следующем открытии.
- Docker/Podman нужен только для isolation.
