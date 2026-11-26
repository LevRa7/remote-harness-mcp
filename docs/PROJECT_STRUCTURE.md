# Структура проекта Remote Harness MCP (`Antigravity-mash`)

> Полный каталог всех директорий и файлов репозитория с описанием их содержимого и назначения. Обновлено после очистки legacy v1 (Google Drive очереди, старый webhook/Apps Script, захардкоженная телеметрия) и синхронизации контрактов MCP v0.7.0.

---

## 1. Корень репозитория (`/`)

| Файл | Что содержит и для чего нужен |
| :--- | :--- |
| `gateway.py` | Облачный/VPS-шлюз FastMCP: терминирует HTTPS/SSE (`/sse`, `/mcp`), держит WebSocket-туннели к узлам, хранит хеши токенов, маршрутизирует публичные шары и монтирует веб-кабинет `/account`. |
| `install.sh` | Универсальный установщик для Linux, macOS и Android/Termux: скачивает модули узла, настраивает Tailscale Funnel / туннель, автозапуск (`systemd`/`launchd`) и CLI `rhm`. |
| `install.ps1` | Установщик для Windows (PowerShell, UTF-8 BOM): находит реальный Python (отсеивая алиас Windows Store), разворачивает узел, службу/автозапуск, трей и watchdog. |
| `install-gui.ps1` | Графический мастер установки для Windows (WinForms, двуязычный RU/EN) с выбором режима, директории и проверкой узла. |
| `install-gui.strings.json` | Локализация (EN/RU) текстовых строк графического установщика `install-gui.ps1`. |
| `install-gui.cmd` | Тонкая `.cmd`-обёртка для запуска `install-gui.ps1` двойным кликом. |
| `build-installer-exe.ps1` | Сборщик однофайлового установщика `RemoteHarnessMCP-Setup-<ver>.exe` (упаковывает `core/`, `ops/`, `skills/` и скрипты в C#-лаунчер через штатный `csc.exe`). |
| `deploy_gateway.sh` | Скрипт деплоя и обновления `gateway.py`, nginx-конфигов и статики установщиков на VPS-шлюзе. |
| `run_tests_windows.ps1` | Локальный раннер тестового сьюта на Windows (поднимает `.win-test-deps/` и гоняет `pytest`). |
| `package.json` | Манифест npm-пакета `remote-harness-mcp`: версия, список публикуемых файлов и бинарные точки входа (`remote-harness-mcp`, `gemini-computer-use`, `rhm`). |
| `VERSION` | Текстовый маркер текущей версии релиза (`0.7.0-rc1-alpha`, источник истины — `core/version.py`). |
| `requirements-gateway.txt` | Python-зависимости шлюза (`PyJWT[crypto]` и др.). |
| `requirements-menu.txt` | Опциональные зависимости консольного меню `rhm` (рендеринг QR-кода). |
| `pytest.ini` | Конфигурация `pytest` для локального и CI-прогона. |
| `.gitignore` / `.dockerignore` / `.npmignore` / `.gitattributes` | Исключения для Git, Docker-сборки, npm-пакета и фиксация LF-переводов строк для shell-скриптов. |
| `LICENSE` | Лицензия MIT. |
| `README.md` / `README_RU.md` | Основная документация проекта на английском и русском языках. |
| `GEMINI.md` | Правила работы AI-агента с репозиторием (инварианты MCP, политика ошибок, Live USB). |
| `ProjectPlan.md` | Текущий план развития Live USB Edition (`scenario="live_usb"`). |
| `Mistakes.md` | Журнал зафиксированных ошибок и архитектурных предохранителей. |
| `CHANGES_AND_STATUS.md` | Сводный журнал изменений, аудита и статуса задач. |

---

## 2. Ядро узла (`core/`)

Весь рантайм узла строится на **Python 3.8+ stdlib** (единственная внешняя runtime-зависимость туннельного агента — `websockets`).

### 2.1. Транспорт, MCP-инструменты и безопасность
| Файл | Назначение |
| :--- | :--- |
| `core/__init__.py` | Инициализация пакета `core`. |
| `core/agent.py` | Туннельный клиент узла: держит WebSocket-соединение к `gateway.py`, принимает JSON-RPC вызовы и пробрасывает их в `mcp_tools`. |
| `core/server.py` | Автономный (standalone) HTTP/SSE/MCP-сервер узла для прямого подключения (например, через Tailscale Funnel без VPS-моста). |
| `core/mcp_tools.py` | Единая реализация всех 33 MCP-инструментов: `mesh_status`, `system_info`, `system_vitals`, `screen_capture`, `list_dir`, `bash_exec`, `read_file`, `read_media`, `write_file`, `edit_file`, `grep_search`, `glob_find`, `win_places`, `batch_read`, `batch_edit`, `run_script`, `fs_index`, `run_job`/`job_*`, `mesh_update`, `node_control`, `schedule`, `device_*`, `share_*`. |
| `core/fs_search.py` | Быстрый BFS-обходчик поверх `os.scandir()` с ранним отсечением тяжёлых папок (`.git`, `node_modules`, `.cache`, `$RECYCLE.BIN` и др.), защитой от циклов symlink/junction и жёстким `time_budget_ms`. |
| `core/fs_index.py` | Локальный фоновый SQLite WAL + FTS5 индекс файлов (`file_index.db`) для поиска по дискам за <30 мс. |
| `core/doc_read.py` | Stdlib-извлечение текста из офисных документов `.docx`, `.xlsx`, `.pptx`, `.odt` (`zipfile` + `xml.etree.ElementTree`, с защитой от zip-бомб) для `read_media`. |
| `core/media.py` | Конвейер `read_media`: превращает локальный файл (изображение, PDF, аудио, видео, Office, текст) в MCP-блоки (`image`/`audio`/`text`) с даунскейлом, кадрами `ffmpeg`, OCR и контролем бюджета base64. |
| `core/web_share.py` | Временная публикация локального файла или директории через локальный loopback-сервер и прокси шлюза/Funnel (`share_file`, `serve_dir`, `share_list`, `unshare`). |
| `core/landing.py` | HTML-страница, которую видит человек при открытии MCP-эндпоинта в браузере (инструкция подключения вместо сырого потока). |
| `core/device.py` | Сбор телеметрии и управление устройством на всех платформах (батарея, сеть, датчики, камера, микрофон, буфер обмена, PIM-данные с охраной через переключатели `MESH_DEVICE*`). |
| `core/vitals.py` | Высокоуровневая обёртка метрик CPU/RAM/диска/uptime поверх платформенных бэкендов. |
| `core/rescue.py` | Схемы и сборщики низкоуровневой диагностики железа и восстановления ОС для режима `live_usb`. |

### 2.2. Настройки, политика, обновления и обслуживание узла
| Файл | Назначение |
| :--- | :--- |
| `core/version.py` | Единый источник версии узла (`__version__`) и `REPO_SLUG`. |
| `core/domain.py` | Единая точка разрешения публичного домена шлюза (env → файл домена → дефолт). |
| `core/paths.py` | Единая точка разрешения runtime-каталогов (`~/.config/remote-harness-mcp`, `~/.cache/...`) с обратной совместимостью со старым именем `antigravity-mesh`. |
| `core/settings.py` | Чтение/запись переключателей узла (`access_mode`, `paused`, `workspace`, `sandbox`, `remote_control`) с единой записью аудита. |
| `core/control.py` | Локальное управление паузой и режимом `read_only` для трея Windows. |
| `core/policy.py` | Единая модель прав и скоупов сессии (`read_only` / `agent`, `READ_ONLY_TOOLS`, `TOOL_SCOPES`). |
| `core/policies.py` | Ограничители и проверки для команд/путей в SDK-харнессе. |
| `core/redact.py` | Редакция секретов, паролей, OAuth- и GitHub-токенов перед записью в логи/телеметрию. |
| `core/rekey.py` | Ротация токена устройства без переустановки узла. |
| `core/sandbox.py` | Профиль изолированного контейнера (Docker/Podman) с монтированием только рабочей директории проекта. |
| `core/schedule.py` | Планировщик таймеров на узле (`after_sec`, `every_sec`, `at="HH:MM"`), переживающий окончание хода чата. |
| `core/goal.py` | Хранение долгоживущей цели сессии (`goal.json`), пробрасываемой в инструкции при переподключении. |
| `core/updater.py` | Автообновление узла из GitHub Releases с проверкой SHA-256, бэкапом и откатом. |
| `core/usage.py` | Локальный журнал вызовов (`usage.jsonl`, только метаданные без пейлоада, с ротацией). |
| `core/menu.py` | Консольное меню `rhm` (статус, QR-код, смена режима/папки, логи, одноразовые CLI-подкоманды). |
| `core/web_settings.py` | Локальная веб-панель настроек узла (`rhm --web`) — браузерный аналог `rhm`. |
| `core/termux.py` | Алиас совместимости для `core/platforms/android/termux.py`. |

### 2.3. Интеграция с Antigravity SDK (опциональный слой)
| Файл | Назначение |
| :--- | :--- |
| `core/agent_harness.py` | Конфигурация `LocalAgentConfig` для запуска через Google Antigravity SDK. |
| `core/schemas.py` | Pydantic-схемы (`NodeVitals` и др.) для типизированной валидации ответов. |
| `core/hooks.py` | Pre/post-хуки безопасности (блокировка опасных команд и очередей). |
| `core/triggers.py` | Триггеры мониторинга состояния узлов и перезагрузки конфигов. |
| `core/subagents.py` | Декларации ролей субагентов SDK (`coordinator_worker`, `remote_node_worker`, `code_researcher`). |
| `core/code_cache.py` | AST-кэширование и инспекция Python-модулей. |

### 2.4. Платформенный слой (`core/platforms/`)
| Путь | Назначение |
| :--- | :--- |
| `core/platforms/__init__.py` | Фабрика выбора бэкенда (`windows`, `linux`, `android`, `darwin`). |
| `core/platforms/base.py` | Базовый абстрактный контракт `PlatformBackend`. |
| `core/platforms/common.py` | Переносимые утилиты форматирования и общие помощники. |
| `core/platforms/encoding.py` | Декодирование вывода дочерних процессов (UTF-8 / OEM / ANSI). |
| `core/platforms/shell.py` | Чистая логика выбора шелла (`powershell` / `cmd.exe` / `git-bash` / `bash`). |
| `core/platforms/png.py` | Минимальный stdlib-энкодер PNG поверх `zlib`. |
| `core/platforms/raster.py` | Чтение, масштабирование и перекодирование растровых изображений для `read_media`. |
| `core/platforms/windows/__init__.py` | Бэкенд Windows (оболочка, телеметрия, обои, буфер обмена, скриншоты). |
| `core/platforms/windows/winapi.py` | Типизированные `ctypes`-прототипы Win32 (`kernel32`, `user32`, `gdi32`, `psapi`, `shell32`, `ole32`). |
| `core/platforms/windows/places.py` | Мгновенный снимок пользовательского контекста `win_places`: Known Folders (`SHGetKnownFolderPath`), stdlib-парсер `.lnk` из `Recent` и обзор дисков. |
| `core/platforms/windows/telemetry.py` | Сбор CPU/RAM/дисков/процессов напрямую через Win32 API без PowerShell/WMI. |
| `core/platforms/windows/process.py` | Проверка живости PID и остановка дерева процессов через `Toolhelp32`. |
| `core/platforms/windows/screen.py` | Снимок виртуального экрана Windows через GDI (`BitBlt` + `CreateDIBSection`). |
| `core/platforms/windows/clipboard.py` | Чтение/запись буфера обмена Windows через `user32`. |
| `core/platforms/linux/__init__.py`, `procfs.py`, `process.py`, `telemetry.py` | Нативный бэкенд Linux через `/proc` и `/sys` без внешних утилит. |
| `core/platforms/android/__init__.py`, `sysfs.py`, `telemetry.py`, `termux.py` | Нативный бэкенд Android/Termux: чтение `/proc`/`/sys` и изолированный мост к `termux-*` (с wake-lock). |
| `core/platforms/darwin/__init__.py`, `clipboard.py`, `process.py`, `screen.py`, `telemetry.py` | Нативный бэкенд macOS (`CoreGraphics`, `libproc`, pasteboard, системные метрики). |

---

## 3. Веб-кабинет аккаунта (`account/`)

Монтируется шлюзом `gateway.py` по префиксу `/account`:

| Файл | Назначение |
| :--- | :--- |
| `account/__init__.py` | Инициализация пакета `account`. |
| `account/app.py` | Веб-приложение личного кабинета: привязка устройств, управление режимами и ссылками. |
| `account/auth.py` | Авторизация через Google OIDC (старт, callback, проверка ID-токена). |
| `account/config.py` | Конфигурация кабинета и валидация переменных OIDC. |
| `account/crypto.py` | Шифрование пользовательских OAuth-токенов на диске. |
| `account/db.py` | SQLite-хранилище пользователей, привязанных узлов и кодов спаривания. |
| `account/drive.py` | Персональный коннектор к Google Drive из кабинета. |
| `account/sessions.py` | Подписанные cookie-сессии без зависимости от `itsdangerous`. |

---

## 4. Точки входа CLI, контейнеры и npm (`bin/`, `docker/`, `npm/`, `templates/`)

| Путь | Назначение |
| :--- | :--- |
| `bin/cli.js` | Основной Node-лаунчер для `npx remote-harness-mcp` / `gemini-computer-use`. |
| `bin/rhm`, `bin/rhm.cmd`, `bin/rhm.js` | Лаунчеры консольного меню `rhm` для POSIX, Windows CMD и npm/npx. |
| `docker/Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `README.md` | Сборка и запуск узла внутри Docker-контейнера. |
| `npm/gemini-computer-use/*` | Пакет-алиас `gemini-computer-use`, перенаправляющий установку на `remote-harness-mcp`. |
| `npm/publish-alias.sh`, `npm/publish-release.ps1` | Скрипты поэтапной публикации релизов в npm. |
| `templates/rhm.service.j2` | Jinja2-шаблон systemd-юнита для Linux-узлов. |

---

## 5. Операционные скрипты и инфраструктура (`ops/`)

| Путь | Назначение |
| :--- | :--- |
| `ops/doctor.ps1` | Интерактивная диагностика установленного Windows-узла (туннель, Python, задачи, логи). |
| `ops/update.ps1` | Скрипт обновления Windows-узла и перезапуска процесса. |
| `ops/mesh-domain.sh` | Shell-резолвер домена проекта (согласован с `core/domain.py`). |
| `ops/windows/agent-watchdog.ps1` | Watchdog для Windows: следит за процессом агента и поднимает его при падении. |
| `ops/windows/rhm-tray.ps1` | Иконка в системном трее Windows (статус, пауза, read-only, открытие логов). |
| `ops/windows/rhm-service.ps1` | Управление Windows-службой узла (включая режим администратора). |
| `ops/windows/run-hidden.vbs` | Скрытый запуск PowerShell-скриптов без мелькающего окна консоли. |
| `ops/windows/README.md` | Документация по Windows-watchdog, трею и doctor. |
| `ops/nginx/remote-harness-mcp.conf`, `mesh-domain.vhost.template`, `render-domain.sh`, `README.md` | Конфигурации Nginx для шлюза и скрипт подстановки домена. |
| `ops/funnel/apply-funnel.sh`, `funnel-mesh.conf`, `repoint-funnel.sh`, `switch-domain.sh`, `verify-oneliners.sh`, `verify-served-installer.sh`, `wsl-verify.sh` | Скрипты настройки и проверки Tailscale Funnel и переключения публичного домена. |
| `ops/live-usb/build-iso.sh`, `firstboot_tui.py` | Конвейер сборки загрузочного Debian LiveUSB-образа с агентом `core.agent` и консольным дашбордом `tty1`. |

---

## 6. Инструменты разработки и релиза (`tools/`)

| Путь | Назначение |
| :--- | :--- |
| `tools/build-payload-zip.py` | Единственный официальный сборщик `dist/mesh-payload-<ver>.zip` + `.sha256` для автообновления узлов. |
| `tools/bump-version.py` | Синхронное обновление версии во всех файлах проекта. |
| `tools/installer-exe/Launcher.cs` | Исходник C#-обёртки для `RemoteHarnessMCP-Setup-*.exe`. |
| `tools/ocr_windows.py` | Скрипт локального OCR на встроенных движках Windows (`WinRT OCR`) для `read_media`. |
| `tools/probe-node.py` | Диагностический клиент для проверки живого узла через шлюз. |
| `tools/verify-curl-bootstrap.py` | Симуляция свежей установки `curl ... \| bash` против публичного шлюза. |
| `tools/platform_check.py`, `tools/platform_check.sh` | Живая проверка Android/Termux-бэкенда прямо на телефоне. |
| `tools/capture-termux-fixtures.sh` | Снятие реальных ответов `termux-api` в `tests/fixtures/termux/`. |
| `tools/build_team_matrix.py`, `tools/check_team_coverage.py` | Генерация и CI-проверка матрицы ответственности и покрытия Wave 1. |
| `tools/poc/local_share_chain_poc.py` | Локальный PoC-стенд цепочки `share -> cabinet -> link_key`. |

---

## 7. Скиллы, CI и вендор (`skills/`, `.agents/`, `.github/`, `vendor/`, `artifacts/`)

| Путь | Назначение |
| :--- | :--- |
| `skills/orchestrator.md` | Актуальный системный скилл, отдаваемый моделью через инструмент `get_orchestration_skill` (правила автономности, батчинга `batch_read`/`batch_edit`, `run_script`, `fs_index`, `device_*`). |
| `.agents/skills/rhm-subagent-mesh/SKILL.md` | Скилл мульти-узловой оркестрации для локальных AI-клиентов. |
| `.agents/skills/rhm-task-dispatcher/SKILL.md` | Скилл диспетчера задач по узлам. |
| `.agents/skills/superpowers/SKILL.md` | Скилл инженерного процесса (brainstorming → spec → plan → TDD). |
| `.github/workflows/ci.yml` | Основной CI-пайплайн: прогон `pytest` на Ubuntu (3.11/3.13) и Windows (3.13), сборка payload-архива, проверка секретов `gitleaks`. |
| `.github/workflows/release.yml` | Релизный пайплайн: сборка артефактов, `.exe`, публикация релиза и заметок из `docs/releases/`. |
| `.github/workflows/team-coverage.yml` | Проверка трассировки задач Wave 1. |
| `vendor/superpowers/` | Вендорная копия методологии `superpowers`. |
| `artifacts/README.md` | Описание локальной папки артефактов (сами артефакты прогонов игнорируются Git). |

---

## 8. Документация (`docs/`)

| Группа / Файлы | Содержимое |
| :--- | :--- |
| **Архитектура и гайды** | `PLATFORMS.md`, `MEDIA.md`, `DOMAIN.md`, `UPDATES.md`, `CI.md`, `CONTAINER.md`, `TERMUX.md`, `GUI_INSTALLER.md`, `SERVICE_ADMIN.md`, `WEB_SETTINGS.md`, `WINDOWS_TESTING.md`, `CHATGPT_CONNECT_RU.md`, `GOOGLE_OAUTH.md`. |
| **Безопасность и аудит** | `ARCH_admin_cannot_access_users.md`, `SANDBOX_SECURITY.md`, `SECURITY_FIXES_main.md`, `SECURITY_FORK_ASSESSMENT.md`, `VULN_REPORT_fork.md`, `REMEDIATION_PLAN_fork.md`, `docs/poc/local_run_2026-11-19.txt`. |
| **Планы и спецификации** | `remote_harness_mcp_plan.md`, `remote_harness_mcp_spec.md`, `remote_harness_mcp_v2_plan.md`, `PLAN_oauth_panel_main.md`, `PLAN_oauth_web_panel.md`, `PLAN_easy_mcp_improvements.md`, `wave-1-auth-account-log-settings.md`, `docs/superpowers/specs/*` и `docs/superpowers/plans/*` (включая `2026-11-26-windows-fast-data-layer-and-batching.md`). |
| **Сравнения и статьи** | `COMPARE_remote_devices_mcp.md`, `easy-mcp-vs-remote-harness-ru.md`, `article_gemini_computer_use.md`, `article_remote_harness_mcp.md`, `docs/reddit/*`. |
| **Релизы и визуал** | `docs/releases/v0.2.3.md` … `v0.7.0-rc1-alpha.md`, `docs/images/*` (скриншоты GUI-установщика), `docs/screenshots/*`. |
| **Процессы Wave 1** | `docs/team/*` (устав, WBS, матрица ответственности, брифы и ревью-отчёты). |

---

## 9. Автотесты (`tests/`)

Более 85 тестовых модулей (`1 700+` проверок), покрывающих все слои без реальной сети и без порчи окружения хоста (`conftest.py` изолирует журнал):

* **Инструменты и Windows Fast Data Layer:** `test_mcp_tools.py`, `test_fs_search.py`, `test_fs_index.py`, `test_win_places.py`, `test_batch_read.py`, `test_batch_edit.py`, `test_run_script.py`, `test_doc_read.py`, `test_read_media.py`, `test_screen_capture.py`, `test_tool_annotations.py`, `test_schedule.py`, `test_goal.py`, `test_node_control.py`, `test_web_share.py`.
* **Платформы и устройства:** `test_platforms.py`, `test_windows_port.py`, `test_windows_ops.py`, `test_windows_alias_guard.py`, `test_device_scenarios.py`, `test_device_tools.py`, `test_termux_adapter.py`, `test_termux_support.py`, `test_phone_payloads.py` + реальные фикстуры `tests/fixtures/termux/*`.
* **Шлюз, безопасность и кабинет:** `test_gateway_*.py`, `test_account_*.py`, `test_policy.py`, `test_security_*.py`, `test_server_hardening.py`, `test_server_http_gate.py`, `test_registry_hardening.py`, `test_redact.py`, `test_sandbox.py`, `test_rekey.py`, `test_approvals.py`.
* **Установщики, версии и совместимость:** `test_installer*.py`, `test_installer.sh`, `test_funnel_installers.py`, `test_updater.py`, `test_version_single_source.py`, `test_domain.py`, `test_domain_single_source.py`, `test_rename_compat.py`, `test_container_and_admin_service.py`, `test_menu.py`, `test_web_settings.py`, `test_tray_script.py`, `test_landing_page.py`, `test_readme_languages.py`.
* **SDK и интеграция:** `test_harness.py`, `test_hooks.py`, `test_policies.py`, `test_policies_without_sdk.py`, `test_schemas.py`, `test_subagents.py`, `test_triggers.py`, `test_code_cache.py`, `test_vitals.py`, `test_mcp_typed_integration.py`, `test_e2e_remote_harness_mcp.py`, `test_resilience_and_perf.py`.
