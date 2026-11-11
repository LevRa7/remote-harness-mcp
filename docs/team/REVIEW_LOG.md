# Журнал ревью и решений (Wave 1)

**Правило:** каждое решение — `Ruling: <что> — <почему> — <цена ошибки>`. Пустой журнал означает «ревью не проводилось», а не «проблем нет».

---

## 1. Статус ревью-раунда №1 (2026-11-11)

| Роль | Отчёт | Вердикт | Blocker | Critical | Статус |
| :--- | :--- | :--- | :---: | :---: | :--- |
| Architect + Critic | `reviews/architect-critic.md` | NOT_READY | 6 | 12 | ✅ |
| API Expert | `reviews/api-expert.md` | NOT_READY | 3 | 3 | ✅ |
| Security Red Team | `reviews/security-red-team.md` | NOT_READY | 4 | 10 | ✅ |
| UI/UX + Desktop | `reviews/ui-desktop-lead.md` | NOT_READY (UI-срез) | 4 | 3 | ✅ |
| QA Test Architect | `reviews/qa-test-architect.md` | NOT_READY (тестовая часть) | 3 | 5 | ✅ |
| Performance Optimizer | `reviews/performance-optimizer.md` | READY_WITH_FIXES | 0 | 5 | ✅ |
| Superpowers Process Auditor | `reviews/superpowers-process-auditor.md` | NOT_READY | 4 | 8 | ✅ |

---

## 2. Findings

> Формат: `F-<номер> | severity | WBS | finding | решение | владелец | статус`.

| ID | Severity | WBS | Finding | Решение | Владелец | Статус |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| API-01 | Blocker | REV-CLM-11 | Pairing-конфликт: план «attach существующий узел», код создаёт новый узел и новые ключи | Один state machine, `pair_secret`, ключи не перевыпускаются | `be-auth` | accepted |
| API-02 | Blocker | REV-CLM-11 | Claim не доказывает владение устройством; credentials отдаются по коду | `pair_secret` + fingerprint + preview/confirm; токен только устройству | `sec-01` | accepted |
| API-03 | Blocker | REV-SEC-09 | `/account` попадает в catch-all `/{user}/{rest}`; `account` не зарезервирован | Reserved names + mount до catch-all + регресс-тест | `api-01` | accepted |
| API-04 | Critical | REV-CAB-15 | Нет единого формата ошибок; клиент не отличает отказ от сбоя | RFC 9457 + MCP `isError{code,retryable,request_id}` | `api-01` | accepted |
| API-05 | Critical | REV-AGT-11 | Контракт per-agent key не определён, два источника истины | `ak_` + единый resolver, `lk_` → default | `be-auth` | accepted |
| API-06 | Critical | REV-OPS-09 | `hello` и «heartbeat с tenant_id» не существуют | `hello` опционален, статус claim — поллингом | `arch-01` | accepted |
| API-08 | Major | REV-CAB-16 | Нет лимитов, сортировки, `Idempotency-Key`, `ETag`, `no-store` | Зафиксировать в OpenAPI и тестах | `api-01` | accepted |
| API-11 | Major | REV-CLM-13 | Нет unlink; удаление аккаунта противоречит «устройство остаётся на связи» | `DELETE …/claim` + правило удаления | `be-auth` | accepted |
| API-13 | Major | REV-AGT-12 | Отзыв агента не может закрыть только его SSE/сессии | Ключ `(user, agent_id)` | `be-data` | accepted |
| API-14 | Critical | REV-SEC-09 | Wildcard CORS применяется к `/account/api/*` | Отдельный scope, `Vary: Origin` | `sec-01` | accepted |
| UI-01 | Blocker | REV-UI-01 | «Выход» останавливает трей, но узел продолжает жить; владелец жизненного цикла не решён | ADR: узел — существующий процесс, трей — наблюдатель; отдельная кнопка «Остановить узел» | `arch-01` | accepted |
| UI-02 | Blocker | REV-CTL-11 | Трей не находит control API: случайный порт, нет endpoint-файла, гонка старта | `control.json` + retry/backoff + version | `desk-tray` | accepted |
| UI-03 | Blocker | REV-CLM-12 | Два сценария привязки и мёртвый `/pair?code=` | Один канонический flow + страница `/pair` | `api-01` | accepted |
| UI-04 | Blocker | REV-UI-01 | Автозапуск split-brain: HKCU Run + Startup .vbs + watchdog | Один владелец; тумблер снимает обе записи; составное состояние | `desk-tray` | accepted |
| UI-05 | Critical | REV-CLM-11 | Код выдаётся без аутентификации устройства | Pair только с device token | `sec-01` | accepted |
| UI-06 | Critical | REV-SEC-09 | Нет `frame-ancestors`/`X-Frame-Options`, wildcard CORS | CSP + DENY для `/account`, сузить CORS | `sec-01` | accepted |
| UI-07 | Critical | REV-UI-04 | CSV formula injection через label/tool | Экранировать `= + - @ TAB CR`, тест | `ui-cab` | accepted |
| UI-09 | Major | REV-UI-02 | Нет состояний `unclaimed/agent_down/paused/update_required`; `approval` — Wave 2 | Матрица состояний, убрать `approval` | `ui-cab` | accepted |
| UI-11 | Major | TRY-04 | «Режим по умолчанию» путается с режимом агента | Переименовать в «Режим новых агентов», показать текущих | `ui-cab` | accepted |
| UI-12 | Major | REV-UI-05 | Нет контракта i18n и плюрализации | Общие ключи, pluralization, тест паритета | `ui-cab` | accepted |
| UI-17 | Major | REV-UI-06 | «Доступность» без критериев WCAG | Чек-лист 2.2 AA + axe + NVDA | `qa-01` | accepted |
| SEC-04 | Blocker | REV-SEC-13 | `MESH_READ_ONLY` не покрывает `bash_exec`/`run_job` | Немедленный guard + allowlist + тест-матрица | `be-data` | accepted |
| SEC-05 | Blocker | REV-SEC-13 | Шлюз не различает агентов/режимы/scopes | Policy-слой до UI-переключателей | `be-data` | accepted |
| SEC-07 | Blocker | REV-SEC-14 | Локальный MCP-сервер: `ACAO:*`, auth выключена без токена | Токен всегда, Origin/Host, JSON-only | `be-data` | accepted |
| SEC-08 | Critical | REV-SEC-11 | Read-only читает `~/.ssh`, `agent.env` | `read_roots` + deny-list | `be-data` | accepted |
| SEC-09 | Critical | REV-SEC-15 | Link-key в plaintext и world-readable реестре | sha256 + prefix, 0600/DACL, атомарная запись | `be-auth` | accepted |
| SEC-10 | Blocker | REV-CLM-11 | Claim не привязан к аккаунту, credentials по коду | `device_claims` с аккаунтом и fingerprint | `be-auth` | accepted |
| SEC-11 | Critical | REV-USE-16 | Ingest без auth, клиентский `device_id` | Device token + `device_id` из auth + лимиты | `be-data` | accepted |
| SEC-14 | Critical | REV-CTL-11 | Control API без `Host`/`Origin`/JSON-only | Allowlist + JSON-only + DACL | `desk-tray` | accepted |
| SEC-20 | Critical | REV-AUTH-20 | Нет step-up для claim/revoke/delete | Fresh-auth ≤ 10 мин | `sec-01` | accepted |
| PB-01 | Blocker | — | Артефактов команды не было на момент ревью | Созданы WBS/matrix/test plan/protocol/log/checker | `planner-01` | resolved |
| B-01 | Blocker | — | Нет заголовка writing-plans | Добавлен Goal/Architecture/Tech Stack/Global Constraints/Review Focus | `planner-01` | resolved |
| B-02/B-03 | Blocker | — | Нет task-level Files/Interfaces/TDD-шагов | Execution Model: task brief на каждый WBS + Red/Green/commit | `planner-01` | accepted |
| B-04 | Blocker | — | Ревью не забюджетировано | +30–45 pd на ревью в оценке спринта | `planner-01` | accepted |
| PERF-02 | Critical | REV-PERF-01 | Синхронный SQLite блокирует event loop шлюза | `asyncio.to_thread` + thread-local + WAL/busy_timeout | `be-data` | accepted |
| PERF-04 | Critical | REV-PERF-02 | Нет ретенции/каскадов; p50/p95 невычислимы из `sum` | TTL + каскад + гистограмма/`max` | `be-data` | accepted |
| PERF-06 | Critical | REV-PERF-03 | Ingest без семантики (дельта/итог), клампов и `updated_at` | Зафиксировать контракт и лимиты | `be-data` | accepted |
| QA-01 | Blocker | REV-QA-01 | Нет трассируемости и тестового каркаса | WBS/matrix/checker + `conftest`/factory/DI | `qa-01` | accepted |
| QA-03 | Blocker | REV-QA-02 | Нет coverage-гейта и JUnit-артефактов | `pytest-cov`, `diff-cover`, upload | `ops-01` | accepted |
| QA-05 | Critical | REV-QA-03 | Нет тестов миграций/отката/chaos | Failpoints, up/down, restore | `be-data` | accepted |
| QA-07 | Critical | REV-CTL-11 | Control API: DNS rebinding/CSRF не проверяются | Host/Origin/JSON-only + ACL | `desk-tray` | accepted |
| QA-08 | Critical | MOD-05 | Матрица «инструмент × режим × слой» неполная | Параметризовать по `TOOL_ANNOTATIONS` | `qa-01` | accepted |
| QA-13 | Major | REV-QA-04 | Нет маркеров/таймаутов/flaky-политики | `pytest-timeout`, `--strict-markers` | `qa-01` | accepted |
| QA-15 | Major | OPS-05 | Устаревший baseline: 434 теста, Python 3.12 | Исправлено: 1258 тестов, 3.11/3.13 | `planner-01` | resolved |
| QA-17 | Major | AUTH-18 | Удаление аккаунта оставляет сирот с PII | Каскад + анонимизация + grace | `be-auth` | accepted |
| QA-18 | Major | REV-QA-05 | Нет манифеста доказательств | `evidence.json` + проверка чекером | `qa-01` | accepted |
| F-01 | Blocker | REV-CLM-11 | Код привязки отдаёт device token/link_key любому, кто знает код | `pair_secret` + выдача только устройству | `sec-01` | accepted |
| F-02 | Blocker | REV-CLM-12 | У claim нет канала завершения: heartbeat — локальный файл, `hello` нет | Поллинг статуса + опциональный `hello` | `arch-01` | accepted |
| F-03 | Blocker | REV-OPS-12 | `deploy_gateway.sh` не доставляет `account/`, шаблоны, static, БД, зависимости | Полный манифест + pip + backup/rollback | `ops-01` | accepted |
| F-04 | Blocker | REV-USE-16 | Ingest без auth; `usage_daily` без `tenant_id` | `/v1/nodes/usage` + tenant из auth | `be-data` | accepted |
| F-05 | Blocker | REV-AGT-11 | Не выбран авторитет per-agent ключей; registry пишется неатомарно | Registry — авторитет, tmp+replace+lock, БД — проекция | `arch-01` | accepted |
| F-06 | Blocker | REV-AGT-13 | Апгрейд переводит legacy link-key в read_only и ломает сценарии | Legacy default = `agent` + баннер/подтверждение | `sec-01` | accepted |
| F-14 | Critical | REV-AUTH-21 | `google_sub` без `iss`/`aud` | UNIQUE(`iss`,`sub`), хранить `aud` | `sec-01` | accepted |
| F-17 | Critical | REV-CLM-14 | Нет носителя кода: трей в Sprint 3, install парринг не использует | CLI `rhm pair` в Sprint 1 | `be-auth` | accepted |
| F-19 | Major | REV-OPS-14 | nginx отдаёт 404 на любой `.json`, `/account` без лимитов | Исключение `/account`, отдельная location | `ops-01` | accepted |
| F-25 | Major | REV-ARC-01 | ADR-004/005/006 не в WBS; у Architect нет строк | ARC-строка + ADR как артефакт | `arch-01` | accepted |
| F-26 | Major | — | Оценки противоречат друг другу; baseline 434 устарел | Пересчитано: 124+30–45 pd, 1258 тестов | `planner-01` | resolved |
| F-28 | Major | REV-OPS-15 | `account/` с Google-секретами попадает на self-host | `MESH_DEPLOYMENT` guard: на selfhost кабинета нет | `arch-01` | accepted |
| F-31 | Major | REV-MOD-09 | Фильтрация `tools/list` ломает parity-тест | Фильтр до `annotate_tools`, parity полного набора | `be-data` | accepted |
| F-32 | Major | REV-MOD-10 | Retry после обрыва может выполнить мутацию дважды | `call_id` + LRU-дедуп + один journal record | `be-data` | accepted |
| SEC-28 | Major | AUTH-13 | Противоречие TTL сессии: 30 дней против idle 30 / absolute 90 | R-020: idle 7 дней / absolute 30 дней + step-up | `sec-01` | accepted |
| SEC-117 | Critical | REV-ARC-02 | Переключатель режима появляется раньше принуждения | Policy до UI, тумблер скрыт до готовности | `arch-01` | accepted |

---

## 3. Rulings (решения контроллера)

| # | Ruling | Почему | Цена ошибки |
| :--- | :--- | :--- | :--- |
| R-001 | Кабинет монтируется в существующий шлюз, отдельный сервис не поднимаем | Экономия 2–3 недель, один домен/БД, меньше движущихся частей | Рост `gateway.py`; выделение в Edge позже потребует аккуратного разделения |
| R-002 | Wave 1 делает авторизацию человека, OAuth MCP-клиентов — Wave 2 | Клиенты неравномерно поддерживают OAuth; link-key уже работает | Кабинет не отзывает отдельного агента по OAuth-токену до Wave 2 |
| R-003 | Отдельные link-key на агента уже в Wave 1 | Иначе «отключить Claude» ломает Gemini | Сложнее миграция legacy `link_key`; закрывается AGT-07 |
| R-004 | Журнал хранит только метаданные, без аргументов/вывода | Приватность и требование «не сливать данные» | Меньше диагностики; при необходимости — отдельный debug-режим с согласием |
| R-005 | Pairing: один state machine с `pair_secret`/fingerprint; `/approve` (admin, legacy enrollment) и `/account/api/claim` — два актора одной функции | Устраняет подмену устройства по коду и конфликт двух контрактов | Нужны миграция `pending_pairs`, экран preview и тест гонки |
| R-006 | Ошибки: RFC 9457 `problem+json` для HTTP + `isError{code,retryable,request_id}` для MCP | Клиент должен отличать отказ политики от сетевого сбоя | Один релиз двойного формата для старых клиентов |
| R-007 | Wave 1 — только `/account/api/*`; `/v1/account/*` зарезервирован за Edge/Wave 3 | Убирает дубли контрактов | При появлении Edge потребуется alias/redirect |
| R-008 | `hello` опционален; без него `proto=1`; при `MESH_ACCOUNT=1` мутации старого узла запрещены (`policy_unavailable`) | Закрывает обход режима через старый узел и не ломает чтение | Старые узлы теряют мутации до обновления — объявить в changelog |
| R-009 | Ключ агента — `ak_` + 43 base64url; параметр `agent=` не вводим, ключ сам резолвится в агента | Один источник identity, нельзя подменить агенту | Нужна миграция legacy `lk_` в default-агента |
| R-010 | Узел остаётся существующим пользовательским процессом (Startup/watchdog); трей — наблюдатель и лаунчер; «Выход» скрывает трей, «Остановить узел» — отдельное действие | Убирает split-brain автозапуска и «фантомный» узел | Нужен ADR и переработка install.ps1/трея |
| R-011 | В Wave 1 в трее нет состояния `approval`; approvals — Wave 2 | Не показывать мёртвое состояние | Состояние появится вместе с approvals |
| R-012 | SQLite: все вызовы через `asyncio.to_thread`, thread-local соединения, WAL, `busy_timeout`, `BEGIN IMMEDIATE` для миграций | Не блокировать SSE/health/tools-call | Чуть больше кода и потоков; тесты на конкурентность |
| R-013 | Usage: хранить `duration_ms_max` + счётчики/гистограмму; ретенция и каскад обязательны | p95 нельзя вычислить из суммы; данные не должны расти вечно | Точность p95 ограничена гистограммой |
| R-014 | Трассируемость — markdown-таблицы + `check_team_coverage.py`; YAML не вводим | Один источник, уже работает в CI | Ручное редактирование таблиц; чекер ловит расхождения |
| R-015 | Бюджет ревью +30–45 pd; один task-reviewer с двумя вердиктами (spec + quality) | Ревью — часть работы, а не бесплатное приложение | Срок +1–2 недели при том же объёме |
| R-016 | `MESH_DEPLOYMENT=vendor|selfhost`; account-слой и Google-секреты только на vendor-хосте | Self-host оператор недоверенный; иначе он получает вход в аккаунты | На selfhost нет кабинета до Edge/Wave 3 |
| R-017 | Legacy `link_key` при апгрейде сохраняет режим `agent` (с баннером), новые агенты — `read_only` | Не ломать существующие сценарии Gemini | Пользователь должен явно ограничить legacy-агента |
| R-018 | `call_id` — обязательный ключ идемпотентности вызова и записи журнала | Retry после обрыва не должен выполнять мутацию дважды | Нужен LRU-дедуп на узле и миграция журнала |
| R-019 | Policy-слой (`bridge/policy.py` + `SessionContext`) внедряется **до** переключателя режима в кабинете | Иначе переключатель — косметика, а read-only обходится | Sprint 2 начинается с MOD-01…06, UI-тумблер включается после |
| R-020 | Сессия: idle 7 дней / absolute 30 дней; опасные действия требуют step-up ≤ 10 мин | Компромисс удобства и защиты украденной cookie | Пользователь чаще логинится при опасных действиях |
| R-021 | Явный режим, выбранный в `rhm`/трее (`access.mode`), — это разрешение оператора; `MESH_READ_ONLY=1` действует только как стартовый fallback до первого выбора | Пользователь не должен получать «конфиг перекрыл моё разрешение»; меню — единственный источник текущего режима | Если оператор ошибся, он меняет режим обратно; env больше не страхует после выбора |
| R-022 | Продуктовый default — **Access on request** (`approval`), а не full | Новый узел не выполняет мутации без явного `a` в `rhm` | Первый вызов может ждать пользователя; тесты выставляют `MESH_ACCESS_MODE=full` явно |
| R-023 | Изолированный контейнер — отдельный профиль `rhm` (`i`), не режим доступа | Изоляция не должна смешиваться с правами read-only/approval/full | Требует Docker/Podman; вне контейнера остаются только host telemetry и device-ветка |
| R-024 | Веб-настройки — только loopback + token + CSRF; мутации через `core.settings` | Не плодить второй источник правды и не светить настройки в сеть | Удалённый доступ только через SSH-туннель |
| R-025 | Сеть контейнера по умолчанию `none`; `I` включает bridge только после `y` | Сомнительный код не должен ходить в LAN/SSH без явного согласия | Установка пакетов внутри требует `I`; после — вернуть strict |

---

## 4. Подписи (approval log)

| WBS | Reviewer | Verdict | Дата | Evidence |
| :--- | :--- | :--- | :--- | :--- |
| — | — | — | — | — |

---

## 5. Открытые вопросы к человеку

| # | Вопрос | Влияние | Решение |
| :--- | :--- | :--- | :--- |
| Q-01 | Google OAuth client id/secret и домен | Блокирует AUTH-01 и реальный вход | ожидается |
| Q-02 | Jinja+htmx vs React | Блокирует CAB-* | рекомендация: Jinja+htmx |
| Q-03 | Windows-only трей в Wave 1 | Блокирует TRY-* на macOS/Linux | рекомендация: Windows-first |

---

## 6. Итог раунда №1 (2026-11-11)

- Проведено 7 независимых ревью + отдельный автор тест-плана; **ни один отчёт не отклонён**, все Blocker/Critical получили диспозицию (`accepted`/`resolved`).
- WBS вырос с 111 до **183** строк (LiveUSB, `rhm`-меню, sandbox, web, Drive); каждая имеет владельца, ревьюера, блокирующий тест и путь доказательства.
- `python tools/check_team_coverage.py --strict` → **exit 0**, 183/183.
- Hotfix-строки: `REV-SEC-13` (read-only guard) и `REV-SEC-14` (node MCP auth) закрыты; `REV-SEC-15` закрыт в объёме 0600/atomic/fail-closed (hash-only миграция `link_key` — Wave 2); `REV-ARC-02` — policy-модуль готов, UI-тумблер ещё не существует.
- Открытые вопросы Q-01…Q-03 остаются за человеком (Google-креды, Jinja/React, Windows-first).

**Ruling:** implementation не начинается, пока не закрыты четыре hotfix-строки выше — иначе кабинет строится поверх обходимого read-only и неаутентифицированного локального сервера.

---

## 7. Реализация hotfix-волны (2026-11-11)

| WBS | Статус | Что сделано | Доказательство |
| :--- | :--- | :--- | :--- |
| REV-SEC-13 | ✅ done | `bash_exec`, `run_job`, `unshare` больше не обходят `MESH_READ_ONLY`; центральный guard в `call_tool` + guard в обработчиках | `artifacts/REV-SEC-13/green.log` (6 passed) |
| REV-SEC-14 | ✅ done | standalone-сервер: токен всегда (генерируется и пишется 0600), `Host`/`Origin` только loopback, JSON-only, CORS без `*`, токен только из заголовка | `artifacts/REV-SEC-14/green.log` (7 passed), `artifacts/REV-SEC-14/http-gate.log` (11 passed) |
| USE-01/11 | ✅ baseline | локальный JSONL-журнал: метаданные, без args/путей/вывода, 0600, ротация 10 МБ × 5 gzip, `clear`, `summary`; подключён к `call_tool` | `artifacts/USE-01/green.log` (5 passed) |
| TRY-01/03/10 | ✅ baseline | `ops/windows/rhm-tray.ps1`: NotifyIcon, статус, pause, live read-only, журнал, папка конфига, автозапуск, рестарт; `-SelfTest`; установщик регистрирует и запускает | `artifacts/TRY-01/tray-selftest.log`, `artifacts/TRY-01/runtime.log` |
| REV-SEC-15 | ✅ hotfix scope | реестр: атомарная запись tmp+`os.replace`+lock, 0600, fail-closed при битом JSON, `registry_corrupt()` блокирует регистрацию/аутентификацию. Хеширование `link_key` (без plaintext) вынесено в Wave 2: контракт `install.sh`/`install.ps1` и повторная регистрация требуют выдачи URL; нужна миграция `link_key_sha256` + ротация | `artifacts/REV-SEC-15/green.log` (5 passed) |
| REV-ARC-02 | ✅ gate holds | `core/policy.py`: `SessionContext`, `READ_ONLY_TOOLS`, scopes, `allowed_tools`, `global_read_only`; per-agent UI-тумблер в кабинете ещё не реализован, поэтому включать нечего. До появления policy-слоя тумблер запрещён | `artifacts/REV-ARC-02/green.log` (6 passed) |
| REV-RESCUE-01…04 | ⏳ parallel | LiveUSB/rescue от отдельного агента: `core/rescue.py`, `ops/live-usb/*`; зарегистрировано в WBS, `rescue_info` разрешён read-only, `rescue_action` — destructive | `ProjectPlan.md`, `Mistakes.md` |
| REV-MENU-01/03/04 | ✅ | `core/menu.py` + `bin/rhm*`: лог, MCP URL, QR (qrcode optional), рабочая директория, `--json`/`--once`, Windows/POSIX клавиши | `artifacts/REV-MENU-01/green.log` (6 passed) |
| REV-MENU-02 | ✅ | три режима `read_only/approval/full`; approval-очередь, одноразовый grant, `approval_required`; `a`/`x` в меню; явный режим перекрывает env | `artifacts/REV-MENU-02/green.log` (10 passed) |
| AUTH-01/02/04/06/09/11/12/14 | ✅ baseline | `account/`: Google start (state/nonce/PKCE), callback, ID-token verify (JWKS/iss/aud/nonce/email_verified), upsert аккаунта/тенанта, сессия, logout | `artifacts/AUTH-01/green.log` (8 passed) |
| CAB-01 | ✅ baseline | `/account` dashboard: email, tenant, device count, logout; `/account` mount до catch-all, CORS не wildcard | `artifacts/AUTH-01/green.log` |
| REV-SANDBOX-01/02/03 | ✅ MVP | `rhm` `i`: Docker/Podman, `.rhm-sandbox/` в проекте, `docker exec` для shell/jobs, конфайнмент путей к `/workspace`; тесты без реального Docker | `artifacts/REV-SANDBOX-01/green.log` (17 passed) |
| REV-SANDBOX-04 | ✅ strict/network | `--network none` по умолчанию, `I` с подтверждением, gVisor/Kata при наличии, cap-drop/no-new-privs/PID/RAM/CPU/ulimit, запрет system-root workspace | `artifacts/REV-SANDBOX-04/green.log` (9 passed) |
| REV-WEB-01 | ✅ | `rhm --web` / `core.web_settings`: loopback, token+CSRF, режимы/папка/isolation/approvals/лог/QR через `core.settings` | `artifacts/REV-WEB-01/green.log` (17 passed) |
| REV-DRIVE-01/02 | ✅ | per-account Drive OAuth (`drive.file`, offline), encrypted refresh-token, `/account/drive` list + disconnect; без реального Google-аккаунта в тестах | `artifacts/REV-DRIVE-01/green.log` (3 passed) |

**Полный прогон:** `1381 passed, 5 skipped, 3 failed` — все три падения в `tests/test_rename_compat.py` относятся к окружению: системный `bash.exe` — WSL без доступа (`HCS_E_ACCESS_DENIED`), а `_restart_posix` на win32 выбирает direct-ветку. На POSIX-хосте эти тесты запускаются штатно; к изменениям не относятся. Лог: `artifacts/FULL/pytest.log`.

**Ограничение:** `REV-SEC-13/14` закрывают дыры в текущем коде, но не заменяют `SessionContext`/policy-слой: per-agent режимы и scopes остаются в Sprint 2 (R-019).
