# LiveUSB: отдельный проект, не часть Wave 1

**Решение (2026-11-11):** Live USB Agent развивается как **отдельный проект**.
`harness-mcp` параллельно развивает узел, кабинет и авторизацию; LiveUSB не
блокирует Wave 1 и не добавляет свои модули в обязательный bootstrap узла.

## Граница

| Компонент | Где живёт | Входит в harness-mcp |
| :--- | :--- | :--- |
| `core/rescue.py` (диагностика/восстановление) | LiveUSB-репозиторий (или optional-плагин) | ❌ нет |
| `ops/live-usb/*` (сборка ISO, firstboot TUI) | LiveUSB-репозиторий | ❌ нет |
| `core/agent.py`, `core/mcp_tools.py`, `core/control.py` | harness-mcp | ✅ да |
| MCP-контракт `rescue_info`/`rescue_action` | версионируется в LiveUSB-репозитории | только опциональная регистрация |

## Как LiveUSB использует harness

1. Собирает LiveUSB-образ, внутри — обычный узел harness (payload/release), без
   форка `core/`.
2. Включает rescue-инструменты как **опциональный модуль**: `MESH_RESCUE=1`
   регистрирует `rescue_info`/`rescue_action` в `TOOLS`/`_HANDLERS`; без флага
   узла они отсутствуют.
3. `rescue_info` — read-only, разрешён в `MESH_READ_ONLY`.
   `rescue_action` — destructive, `dry_run=True` по умолчанию, обязан вызывать
   `core.mcp_tools._read_only_active()`.
4. Контракт MCP-инструментов и тесты (`tests/test_rescue.py`) живут в
   LiveUSB-репозитории; harness не ломается, если модуля нет.

## Что сделать при выносе

- [ ] Создать репозиторий `remote-harness-liveusb` и перенести `core/rescue.py`,
      `ops/live-usb/*`, `ProjectPlan.md`, `Mistakes.md`.
- [ ] Убрать `core/rescue.py` из `install.sh`/`install.ps1` bootstrap и из
      `REV-RESCUE-01…04` (или пометить `SEP-*` вне Wave 1).
- [ ] В harness оставить optional hook: `MESH_RESCUE=1` + `rescue_info` в
      read-only allowlist; `rescue_action` не включать без dry-run.
- [ ] В LiveUSB-репозитории закрепить версию harness (release tag) и smoke-тест
      «узел поднимается без rescue».
- [ ] Перенести `tests/test_rescue.py` и фикстуры `lsblk/smartctl` в LiveUSB CI.

## До выноса

Файлы параллельного агента уже лежат в рабочем дереве. Они **не входят в
релиз Wave 1**: в WBS отмечены как `REV-RESCUE-01…04` (отдельный backlog), в
`REVIEW_LOG` — «parallel, не блокирует». `install.sh`/`install.ps1` временно
включают `core/rescue.py`, чтобы bootstrap-тест не падал; при выносе эти строки
удаляются вместе с модулем.
