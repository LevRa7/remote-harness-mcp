# План: Tailscale Funnel вместо VPS-моста (standalone-узел)

> Ветка: `feat/tailscale-funnel-standalone`. Спека: обсуждение в сессии 2026-11-23.
> Формат: Superpowers `executing-plans` (inline, один контекст, финальное ревью ветки).

## Проблема

Узел в standalone-режиме защищён от DNS-rebinding: `host_allowed()` принимает только
loopback-имена и точный порт (`core/server.py:712`). Tailscale Funnel приходит на
`127.0.0.1`, но с `Host: <node>.<tailnet>.ts.net`, поэтому любой запрос получает 403 —
включая `/sse`. Кроме того, standalone-ветка `install.ps1` не регистрирует автозапуск
и не умеет поднимать Funnel, поэтому схема «без VPS» требует ручных шагов после каждой
перезагрузки.

## Задачи

### TSF-01 — Allowlist публичных Host/Origin

`MESH_ALLOWED_HOSTS` (CSV точных имён) расширяет `host_allowed()`/`origin_allowed()`.

Критерии приёмки:
- имя из allowlist принимается с портом и без (`node.ts.net`, `node.ts.net:443`);
- `Origin: https://node.ts.net` принимается, `http://node.ts.net` — нет;
- wildcard (`*`, `*.ts.net`), порт в значении переменной и мусор отвергаются;
- `evil.example`, `127.0.0.1.evil.example` по-прежнему 403;
- без переменной поведение не меняется.

### TSF-02 — Узел читает `standalone.env`

`core.server` подхватывает `MESH_*` из `~/.config/remote-harness-mcp/standalone.env`
(окружение приоритетнее файла), `MESH_TOKEN` оттуда используется, когда `--token` не задан.
Это убирает секрет из командной строки автозапуска.

Критерии приёмки:
- ключ из файла попадает в `os.environ`, если его там нет;
- значение из окружения не перетирается файлом;
- отсутствие файла не роняет запуск;
- `--token` по-прежнему главнее файла.

### TSF-03 — Установщик: Funnel + автозапуск

`install.ps1 -Mode standalone` получает `-Funnel`:
- определяет ts.net-имя через `tailscale status --json` (Self.DNSName);
- пишет `MESH_TOKEN` и `MESH_ALLOWED_HOSTS` в `standalone.env`;
- запускает `tailscale funnel --bg --https=443 http://127.0.0.1:<Port>`;
- печатает публичную ссылку `https://<name>/sse?token=…`;
- регистрирует задачу `RemoteHarnessMCPNode` (AtLogOn + restart on failure).

Критерии приёмки (static-тесты, как в `tests/test_installer_windows.py`):
- скрипт содержит `MESH_ALLOWED_HOSTS`, `tailscale funnel`, `RemoteHarnessMCPNode`;
- без `-Funnel` поведение прежнее;
- при отсутствии `tailscale` установщик не падает, а объясняет, что поставить.

### TSF-04 — Документация

Раздел в `README_RU.md` и `README.md`: схема, команды, ограничения Funnel, шаги пользователя.

### TSF-05 — Верификация

`pytest tests/test_server_hardening.py tests/test_server_http_gate.py tests/test_installer_windows.py`,
затем полный прогон, артефакты в `artifacts/TSF-01/`, финальное ревью ветки.

## Rulings

- Порт для allowlist-хоста не проверяется: имя, которым не управляет атакующий, не даёт
  rebound-вектора, а edge всегда ставит 443. — иначе Funnel ломается на `Host: name:443`.
- `MESH_ALLOWED_HOSTS` читается на каждый запрос, а не кешируется: переменная окружения
  процесса не меняется, кеш только добавил бы класс ошибок. — цена: парсинг строки на запрос.
- Автозапуск через планировщик, а не через Startup-папку: нужен restart-on-failure.
- Матрицу `docs/team/OWNERSHIP_MATRIX.md` не трогаем: она про Wave 1 (AUTH-*), эта работа
  вне WBS. — если появится требование, добавим строку TSF-*.

## Результат

| Задача | Доказательство |
| :--- | :--- |
| TSF-01 | RED: 5 падений в `tests/test_server_hardening.py` (`allowed_public_hosts` отсутствовал) + e2e 403 вместо 401. GREEN: 45 passed |
| TSF-02 | RED: `apply_standalone_env` отсутствовал (5 падений `tests/test_standalone_env.py`). GREEN: те же 45 |
| TSF-03 | RED: на `HEAD:install.ps1` отсутствуют `MESH_ALLOWED_HOSTS`, `Get-TailnetName`, `funnel --bg --https=443`, `RemoteHarnessMCPNode`, `[switch]$Funnel`. GREEN: 5 static-тестов + `Parser::ParseFile` = 0 ошибок |
| TSF-04 | разделы в `README.md` / `README_RU.md` |
| TSF-05 | полный прогон: `1603 passed, 8 skipped in 150.93s`; логи в `artifacts/TSF-01/` |

## Rulings (по ходу работы)

- `"/"` + `"` в `$taskCommand` невалиден в PowerShell (экранирование — обратная кавычка, не `\`):
  заменил на конкатенацию с одинарными кавычками. — иначе установщик не парсится.
- Работа вынесена в отдельный worktree `D:\MyProjects\rhm-funnel`: основной чекаут
  одновременно правил другой процесс (`core/paths.py`, `install.sh`, `docker/`, `-AdminService`),
  и файл `install.ps1` в нём был синтаксически сломан чужой незавершённой правкой.
  — изоляция вместо гонки за один и тот же файл; цена — два чекаута.
- `standalone.env` пишется двумя строками (`MESH_TOKEN`, `MESH_ALLOWED_HOSTS`): один файл —
  один источник и для токена, и для allowlist. — иначе автозапуск и ручной запуск разъедутся.
