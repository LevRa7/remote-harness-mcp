# План улучшений Remote Harness MCP (по итогам сравнения с Easy MCP)

Источник анализа: `docs/easy-mcp-vs-remote-harness-ru.md`  
Целевая версия: `0.7.0-rc1-alpha` → `0.7.0`  
Дата создания: 2026-11-26

> **Правило для субагентов:** строго следовать задачам своего этапа, не редактировать чужие файлы в параллельных волнах, проверять все изменения тестами (`pytest`) и фиксировать статус, изменённые файлы и точные результаты тестов.

---

## Сводка статусов

| ID | Этап | Задача | Целевые файлы | Статус | Тесты / Проверка |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1.1** | Этап 1 | Детекция бинарников в `read_file` с подсказкой `read_media` | `core/mcp_tools.py` | `[x] DONE` | `tests/test_mcp_tools_improvements.py` (12 passed) + регрессия 143 passed |
| **1.2** | Этап 1 | Запрет двойного бинда порта на Windows (`SO_EXCLUSIVEADDRUSE`) | `core/server.py` | `[x] DONE` | `tests/test_server_usage_oauth_improvements.py` (2 теста, всего 61 passed) |
| **1.3** | Этап 1 | Ротация и асинхронная запись журнала вызовов | `core/usage.py`, `core/server.py` | `[x] DONE` | `tests/test_server_usage_oauth_improvements.py` (3 теста) + `tests/test_usage_log.py` (5 passed) |
| **1.4** | Этап 1 | Синхронизация `README.md`/`README_RU.md` с `tools/list` + CI-тест | `README.md`, `README_RU.md`, `tests/test_readme_tools_sync.py` | `[x] DONE` | `tests/test_readme_tools_sync.py` (4 passed) + `tests/test_readme_languages.py` + полная регрессия 1807 passed |
| **2.1** | Этап 2 | Сжатие описаний `tools/list` и профили (`MESH_TOOLSETS` / автопрофиль ОС) | `core/mcp_tools.py`, `core/settings.py` | `[x] DONE` | `tools/list`: 33 517 → 16 897 Б (−49,6%), `core`: 10 567 Б (−68,5%); 143 passed |
| **2.2** | Этап 2 | Компактный вывод `grep_search` (относительные пути `path:line: text`) | `core/fs_search.py`, `core/mcp_tools.py` | `[x] DONE` | `tests/test_fs_search_and_policies_improvements.py` (59 passed) + `test_mcp_tools_improvements.py` |
| **2.3** | Этап 2 | Ускорение литерального полного прохода в `grep_search` (bytes pre-filter) | `core/fs_search.py` | `[x] DONE` | `tests/test_fs_search_and_policies_improvements.py` + `tests/test_fs_search.py` (59 passed) |
| **3.1** | Этап 3 | Инструменты `git_status` и `git_diff` без оболочки | `core/mcp_tools.py` | `[x] DONE` | `tests/test_mcp_tools_improvements.py` (12 passed) |
| **3.2** | Этап 3 | Инструмент `fetch_url` с защитой от SSRF | `core/mcp_tools.py` | `[x] DONE` | `tests/test_mcp_tools_improvements.py` (12 passed) |
| **3.3** | Этап 3 | Запрет записи в `.git/hooks/*` и `.git/config` + флаг `--no-shell` / `MESH_ENABLE_SHELL=0` | `core/policies.py`, `core/mcp_tools.py`, `core/server.py` | `[x] DONE` | `test_fs_search_and_policies_improvements.py` (41 passed) + `test_mcp_tools_improvements.py` + `test_server_usage_oauth_improvements.py` |
| **4.1** | Этап 4 | Лёгкий режим `--folder <dir>` (+ `--quick-tunnel` при наличии `cloudflared`) | `core/server.py` | `[x] DONE` | `tests/test_server_usage_oauth_improvements.py` (5 тестов, всего 61 passed) |
| **4.2** | Этап 4 | Минимальный OAuth 2.1 в standalone-режиме | `core/server.py` | `[x] DONE` | `tests/test_server_usage_oauth_improvements.py` (2 e2e-теста PKCE S256, всего 61 passed) |
| **4.3** | Этап 4 | Раздел Threat Model в `README.md` и `README_RU.md` | `README.md`, `README_RU.md` | `[x] DONE` | `tests/test_readme_tools_sync.py` (`test_both_readmes_include_threat_model_section`) + полная регрессия 1807 passed |

---

## Детализация задач и критерии приёмки

### Этап 1. Быстрые победы: стабильность и гигиена

- **1.1. Детекция бинарных файлов в `read_file`** — `[x] DONE`
  - Читать первые 8192 байта файла в бинарном режиме; при наличии NUL-байта (`b"\x00"`) отказывать в текстовом чтении и возвращать структурированное сообщение с размером файла, расширением/MIME и рекомендацией использовать `read_media`.
- **1.2. Запрет двойного бинда порта на Windows** — `[x] DONE`
  - В `core/server.py` для `_StandaloneHTTPServer` на `sys.platform == "win32"` выставлено `allow_reuse_address = False` и `socket.SO_EXCLUSIVEADDRUSE` в `server_bind`.
- **1.3. Ротация и асинхронная запись журнала вызовов** — `[x] DONE`
  - В `core/usage.py` добавлены `MAX_BYTES` (1 МБ), `KEEP_GENERATIONS` (3), `_rotate`, асинхронная очередь `_QUEUE` (`queue.SimpleQueue`) + daemon-поток `rhm-usage-writer` с `flush()` и `atexit`.
- **1.4. Синхронизация README и `tools/list` + тест в CI** — `[x] DONE`
  - Обновлены `README.md` и `README_RU.md`: зафиксировано 36 инструментов на узле (`ALL_TOOLS`, 38 через шлюз), описаны профили `MESH_TOOLSETS`, автопрофиль платформы (34 на desktop), новые инструменты `git_status`, `git_diff`, `fetch_url` и режимы `--folder`, `--no-shell`, `--quick-tunnel`, OAuth 2.1 (PKCE S256).
  - Добавлен контрактный тест `tests/test_readme_tools_sync.py` (4 теста).

### Этап 2. Экономия контекста и ускорение поиска

- **2.1. Сжатие `tools/list` и профили инструментов (`MESH_TOOLSETS`)** — `[x] DONE`
  - Сжаты описания в `core/mcp_tools.py`: база (33 инструмента) 33 517 → 16 897 Б (−49,6%); автопрофиль desktop (34 инструмента) 17 461 Б (−47,9%); `all` (36 инструментов) 18 904 Б (−43,6%); `core` (22 инструмента) 10 567 Б (−68,5%).
  - Добавлены `TOOLSET_GROUPS`, `parse_toolsets()`, `list_tools()`, `core/settings.py` (`toolsets_setting`, `shell_enabled_setting`).
- **2.2. Компактный вывод `grep_search`** — `[x] DONE`
  - Добавлены `format_grep_compact` в `core/fs_search.py` и параметр `format="compact"|"json"` в `core/mcp_tools.py` (плюс поле `compact` при обратной совместимости).
- **2.3. Ускорение литерального прохода в `grep_search`** — `[x] DONE`
  - В `core/fs_search.py` чтение сведено к одному бинарному открытию + быстрый `needle_bytes in raw_bytes` для литеральных шаблонов (`_is_literal_regex`) при `ignore_case=False`.

### Этап 3. Недостающие инструменты и защитные мелочи

- **3.1. `git_status` и `git_diff` без оболочки** — `[x] DONE`
  - Добавлены `git_status` и `git_diff` (`readOnlyHint=True`, `subprocess.run(..., shell=False)` с `-c core.fsmonitor=false -c core.hooksPath=`).
- **3.2. `fetch_url` с защитой от SSRF** — `[x] DONE`
  - Добавлен `fetch_url` (`readOnlyHint=True`, только `https://`, проверка всех резолвленных IP через `ipaddress`, до 5 редиректов, лимит 300 КБ, `strip_html=True`).
- **3.3. Защита `.git/hooks` / `.git/config` и режим `--no-shell`** — `[x] DONE`
  - В `core/policies.py` добавлены `is_protected_git_path` и `check_write_allowed`; подключены в `write_file`, `edit_file`, `batch_edit`; `MESH_ENABLE_SHELL=0` проверяется в `bash_exec`, `run_script`, `run_job` и `core/server.py`.

### Этап 4. Снижение порога входа

- **4.1. Режим «одна папка за минуту» (`--folder` / `--quick-tunnel`)** — `[x] DONE`
  - В `core/server.py` добавлены `--folder`, `--no-shell`, `--quick-tunnel` (`start_quick_tunnel`, `parse_quick_tunnel_url`, автообновление `MESH_ALLOWED_HOSTS` / `MESH_PUBLIC_URL`).
- **4.2. Минимальный OAuth 2.1 в standalone-режиме** — `[x] DONE`
  - В `core/server.py` реализованы `StandaloneOAuthStore` и маршруты `GET /.well-known/oauth-authorization-server`, `GET /.well-known/oauth-protected-resource`, `POST /oauth/register`, `GET /oauth/authorize` (PKCE S256), `POST /oauth/token`.
- **4.3. Раздел Threat Model в README** — `[x] DONE`
  - Добавлены разделы **Threat Model** (`README.md`) и **Модель угроз** (`README_RU.md`) в верхней части документации (защита по умолчанию: loopback bind, обязательный токен / OAuth 2.1 PKCE S256, запрет записи в `.git/hooks` и `.git/config`, SSRF-защита в `fetch_url`, `--folder`, `--no-shell`, `read_only`, Docker-песочница; остаточные риски при включённой оболочке). Проверяется в `tests/test_readme_tools_sync.py`.

---

## Журнал выполнения

### Волна 1 (2026-11-26) — Выполнено
- **Сервер, журнал вызовов и OAuth (`core/server.py`, `core/usage.py`, `tests/test_server_usage_oauth_improvements.py`)**: задачи 1.2, 1.3, 4.1, 4.2.
  - Прогон: `py -3.13 -m pytest -v -p no:cacheprovider tests/test_server_usage_oauth_improvements.py tests/test_usage_log.py tests/test_server_hardening.py tests/test_server_http_gate.py tests/test_standalone_env.py tests/test_standalone_shares.py tests/test_landing_page.py tests/test_mcp_endpoints.py` → **61 passed**.
- **Поиск и политики (`core/fs_search.py`, `core/policies.py`, `tests/test_fs_search_and_policies_improvements.py`)**: задачи 2.2, 2.3, 3.3.
  - Прогон: `py -3 -m pytest -v tests/test_fs_search_and_policies_improvements.py tests/test_fs_search.py tests/test_policies.py tests/test_policies_without_sdk.py tests/test_policy.py` → **59 passed**.
- **MCP-инструменты и настройки (`core/mcp_tools.py`, `core/settings.py`, `tests/test_mcp_tools_improvements.py`)**: задачи 1.1, 2.1, 2.2, 3.1, 3.2, 3.3.
  - Прогон: `pytest tests/test_mcp_tools_improvements.py tests/test_mcp_tools.py tests/test_fs_search.py tests/test_batch_edit.py tests/test_batch_read.py tests/test_run_script.py tests/test_tool_annotations.py tests/test_policy.py tests/test_web_settings.py tests/test_node_control.py tests/test_web_share.py -q` → **143 passed, 1 skipped**.

### Волна 2 (2026-11-26) — Выполнено
- **Интеграция `core/server.py` ↔ `core/mcp_tools.py` и синхронизация шлюза (`core/server.py`, `gateway.py`, `tests/test_mcp_tools.py`)**:
  - В `core/server.py` переведён `TOOL_DEFINITIONS` на `mcp_tools.ALL_TOOLS`, добавлен `"fetch_url": ("url",)` в `_REQUIRED_ARGS`, а `RemoteHarnessMCPServer.list_tool_specs()` подключён к `mcp_tools.list_tools()` (с сохранением пользовательских инструментов из `register_tool()`).
  - В `gateway.py` синхронизированы описания и схемы 4 инструментов ветки `device_*` со сжатыми спецификациями из `core/mcp_tools.py` (исправлено единственное падение в `tests/test_gateway_share_route.py::test_the_four_device_tools_match_the_node_field_for_field`).
  - В `tests/test_mcp_tools.py` обновлён контракт `test_server_exposes_all_tools_and_jsonrpc_list` под `mcp_tools.list_tools()` и добавлен тест `test_server_tools_list_respects_mesh_toolsets`.
- **Документация и контрактные тесты (`README.md`, `README_RU.md`, `tests/test_readme_tools_sync.py`)**: задачи 1.4 и 4.3.
- **Изменённые / созданные файлы Волны 2**: `core/server.py`, `gateway.py`, `README.md`, `README_RU.md`, `tests/test_mcp_tools.py`, `tests/test_readme_tools_sync.py`, `docs/PLAN_easy_mcp_improvements.md`.
- **Полный регрессионный прогон**: `py -3 -m pytest -q -p no:cacheprovider` (в `D:/MyProjects/Antigravity-mash`) → **1807 passed, 22 skipped, 0 failed** (158.13s).
