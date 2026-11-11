# Wave 1 — «Аккаунт и контроль»: авторизация, кабинет, локальный журнал, меню настроек

**Срок:** 8 недель (7 недель разработки + 1 неделя стабилизации и ревью)
**Команда:** 5–5,5 FTE
**Оценка:** ≈ 154–169 pd (124 pd разработки + 30–45 pd ревью)
**База:** Remote Harness MCP v0.4.8
**Связанный документ:** [remote_harness_mcp_v2_plan.md](remote_harness_mcp_v2_plan.md)
**Команда и контроль:** [docs/team/AGENT_TEAM.md](team/AGENT_TEAM.md), [WBS.md](team/WBS.md), [OWNERSHIP_MATRIX.md](team/OWNERSHIP_MATRIX.md), [TEST_PLAN.md](team/TEST_PLAN.md), [VERIFICATION_PROTOCOL.md](team/VERIFICATION_PROTOCOL.md)

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`. Each WBS row becomes a task brief with Files, Interfaces, a failing test, the verification command, and a commit; a fresh implementer executes it and a fresh reviewer gates it.

**Goal:** выпустить контур управления: Google-вход, привязка устройства, кабинет устройств/агентов/статистики, локальный журнал и меню настроек.

**Architecture:** пакет `account/` монтируется в существующий Starlette-шлюз по `/account`; SQLite + SQL-миграции; трей Avalonia общается с узлом по loopback control API; метаданные использования хранятся локально в JSONL, в кабинет уходят только суточные агрегаты.

**Tech Stack:** Python 3.11 (floor) / 3.13 (current), Starlette, SQLite, Jinja2 + htmx, Avalonia 11 / .NET 8, pytest, Playwright, GitHub Actions.

**Spec:** этот документ + [remote_harness_mcp_v2_plan.md](remote_harness_mcp_v2_plan.md) §3–5; требования — [WBS.md](team/WBS.md) (183 строки).

## Global Constraints

- `MESH_ACCOUNT=1` включает кабинет; при `0` поведение и маршруты 0.4.8 не меняются.
- Legacy `/sse`, `/mcp`, `/messages`, `?user=&token=` и старые узлы 0.4.x обязаны работать.
- Ключи: `lk_` — legacy link-key, `ak_` — ключ агента (`ak_` + 43 base64url); cookie — `__Host-rhm_session`.
- Claim: код 8 символов, TTL 10 минут, `pair_secret` только у устройства, ключи при claim не перевыпускаются.
- В журнале и статистике нет аргументов, путей, вывода, токенов; только метаданные.
- Один WBS-ID = один владелец + один независимый ревьюер + минимум один блокирующий тест.
- Любое утверждение «готово» подтверждается свежим выводом команды и артефактом в `artifacts/<WBS>/`.

## Review Focus

1. Повторный/подменённый OAuth-callback и `state` — не должен создавать сессию или путать аккаунты.
2. Claim чужого устройства по подсмотренному коду — не должен привязывать узел без `pair_secret`/fingerprint.
3. Старый узел без `ctx`/`hello` — не должен получать мутации при включённом кабинете и не должен ломать запись.
4. Утечка аргументов/секретов в JSONL, CSV и ingest — не должна происходить ни при каком инструменте.
5. Гонка старта трея и узла, stale `control.json`, выключение автозапуска — не должны оставлять «фантомный» узел или мёртвый трей.

## Execution Model (Superpowers)

1. Planner нарезает спринт на WBS-строки и для каждой создаёт task brief: `Files`, `Interfaces`, шаг Red (код теста + ожидаемый FAIL), Green, команда проверки, commit.
2. Свежий implementer-субагент выполняет один brief; не диспатчит своих ревьюеров.
3. Свежий task-reviewer проверяет spec + quality по diff `BASE..HEAD`, не тот же агент, что делал.
4. Fix-loop ≤ 5 раундов; решения фиксируются в `REVIEW_LOG.md` как `Ruling`.
5. Финальный whole-branch review — агент, не ревьюивший ни одной задачи; затем `finishing-a-development-branch`.

---

## 1. Решение

Первый выпуск v2 — не Edge, не LiveUSB и не федерация, а **контур управления**:

1. **Авторизация человека** через Google и привязка устройства к аккаунту.
2. **Личный кабинет**: список устройств и агентов, переименование, отключение, права, статистика.
3. **Локальный журнал использования** на устройстве: что вызывали, когда, чем закончилось — без содержимого команд.
4. **Меню настроек** в трее: автозапуск, активность, режим, рабочая папка, журнал, переход в кабинет.

Это закрывает пункты **3 (режим) и 5 (рабочая папка) полностью**, а пункты **1 (меню/журнал) и 4 (кабинет)** — в объёме настроек, устройств, агентов, прав и статистики. Запросы разрешений (approvals), удалённые команды, шеринг файлов и hover-панель остаются на Wave 2. Зато появляется фундамент для остальных: `Account/Tenant/Device/AgentConnection`, сессии, права, события использования, control API.

### 1.1. Что важно различать

| Авторизация | Что это | Когда |
| :--- | :--- | :--- |
| **A. Авторизация человека** | Google-вход в кабинет, сессия, привязка устройства | **Wave 1** |
| **B. Авторизация MCP-клиента** | OAuth 2.1/DCR/consent для Claude/ChatGPT, токены агентов | Wave 2 |
| **C. Авторизация self-host моста** | Google-привязка собственного хоста, миграция | Wave 3 |

В Wave 1 MCP-клиенты продолжают ходить по link-key; кабинет лишь показывает их как агентов и позволяет отозвать. Это осознанно: OAuth для облачных клиентов требует edge и конформанс-тестов, а ценность кабинета достижима без него.

---

## 2. Границы выпуска

### Входит

- Google OIDC (authorization code + nonce/state), сессии, CSRF, выход, удаление аккаунта.
- Привязка устройства кодом (device claim), наследование существующих узлов.
- Кабинет: обзор, устройства, карточка устройства, агенты, статистика, настройки, права (матрица групп инструментов).
- Агенты: запись `AgentConnection`, отдельные link-key на агента, режим, scopes, рабочая папка, отзыв.
- Локальный журнал: JSONL + ротация, сводка, экспорт CSV, очистка, опциональная отправка суточных агрегатов в кабинет.
- Трей (Windows в первую очередь): иконка, меню настроек, автозапуск, пауза, режим по умолчанию, рабочая папка, журнал, кабинет.
- Принуждение режима/прав на трёх уровнях (мост, токен, узел), в объёме mode + группы инструментов.

### Не входит (переносится)

| Что | Куда |
| :--- | :--- |
| OAuth для MCP-клиентов, consent-экран, DCR | Wave 2 |
| Hover-панель с текстом запроса и abort | Wave 2 |
| Per-agent переключатель в трее (в кабинете — входит) | Wave 2 |
| Полная матрица approvals («всегда/один раз», таймауты) | Wave 2 |
| Edge, alias `/d/<id>`, fallback, миграция | Wave 3 |
| LiveUSB, Docker, self-host установщик | Wave 4 |
| Blind-режим, E2E-шары, аудит Merkle | Wave 5 |

---

## 3. Архитектура Wave 1

### 3.1. Схема

```
Браузер пользователя ──HTTPS──┐
                              ▼
                 ┌──────────────────────────────┐
                 │ gateway.py (существующий хост)│
                 │  ├─ MCP/SSE/WS (как сейчас)   │
                 │  ├─ /account  (Jinja + htmx)  │
                 │  ├─ /account/api (JSON)       │
                 │  └─ account/ (SQLite)         │
                 └───────┬──────────────┬────────┘
                         │ WS-туннель   │ Google OIDC
                         ▼              ▼
        ┌────────────────────────┐   ┌──────────────┐
        │ NODE (agent + tray)    │   │ accounts.google│
        │  ├─ core/usage.py      │   └──────────────┘
        │  ├─ core/control.py    │
        │  └─ desktop/RhmTray    │
        └────────────────────────┘
```

**Почему кабинет живёт рядом со шлюзом, а не в отдельном сервисе:**

- уже есть публичный домен, TLS и `PUBLIC_BASE_URL`;
- шлюз знает `active_tunnels` и может отдавать онлайн-статус без внутреннего API;
- одна SQLite-БД, нет межсервисной аутентификации;
- меньше движущихся частей → быстрее первый выпуск.

Плата: `gateway.py` вырастет. Поэтому код — в отдельном пакете `account/`, а в `gateway.py` только `Mount` и 3–4 хука. Когда появится Edge (Wave 3), `account/` переезжает почти без изменений: БД и маршруты уже отделены.

### 3.2. Компоненты

| Компонент | Ответственность | Новый/изменён |
| :--- | :--- | :--- |
| `account/` | OIDC, сессии, аккаунты, тенанты, устройства, агенты, статистика, шаблоны | Новый |
| `account/migrations/` | SQL-миграции SQLite | Новый |
| `account/templates/` | Jinja2-страницы кабинета | Новый |
| `account/static/` | CSS/JS (htmx, графики — SVG/Canvas без тяжёлых библиотек) | Новый |
| `core/usage.py` | Локальный журнал, ротация, сводка, экспорт | Новый |
| `core/control.py` | Локальный HTTP/SSE API для трея | Новый |
| `desktop/RhmTray/` | Avalonia 11, иконка, меню, окно журнала | Новый |
| `gateway.py` | Монтирование `account`, хуки `initialize`, проверка agent-key, отдача `ctx` в туннель | Изменён |
| `core/agent.py` | Приём `ctx`, применение `SessionContext`, публикация состояния в control API | Изменён |
| `core/mcp_tools.py` | `SessionContext` вместо глобального `_READ_ONLY`; проверки scopes | Изменён |
| `install.sh`, `install.ps1` | Опциональная установка зависимостей кабинета и трея | Изменён |

### 3.3. Развёртывание

- Фича-флаг `MESH_ACCOUNT=1`; без него шлюз работает как раньше.
- Зависимости шлюза (опциональные): `jinja2`, `PyJWT[crypto]`, `httpx` (или stdlib `urllib` + `PyJWT`).
- Конфиг: `MESH_GOOGLE_CLIENT_ID`, `MESH_GOOGLE_CLIENT_SECRET`, `MESH_SESSION_SECRET` (32+ байт), `MESH_ACCOUNT_BASE_URL`, `MESH_ACCOUNT_DB=/var/lib/rhm/account.db`.
- Если зависимости/конфиг отсутствуют — кабинет отдаёт понятную страницу «не настроен», MCP-транспорт не страдает.
- Для разработки: `MESH_AUTH_MODE=dev` — вход только с localhost по фиксированному email; в production запрещён (проверка на старте).

---

## 4. Спринты

### Sprint 0 — решения и каркас (3–4 дня)

**Задачи.**

- [ ] ADR-004: Google OIDC, модель сессий, cookie-политика, привязка устройства.
- [ ] ADR-005: где живёт кабинет (в шлюзе) и как потом выделяется в Edge.
- [ ] Каркас `account/`: `__init__.py`, `app.py`, `db.py`, `auth.py`, `routes.py`, `templates/`.
- [ ] `requirements-gateway.txt` + проверка зависимостей на старте.
- [ ] Миграция `0001_init.sql` (accounts, sessions, tenants, devices, agents, usage_daily, audit_events, device_claims).
- [ ] Фейковый OIDC-провайдер для тестов: `tests/fake_oidc.py` (RSA-ключ, JWKS, authorize/token).
- [ ] Фича-флаг, конфиг, CI-джоб с зависимостями.
- [ ] Решить: Jinja + htmx (рекомендуется) или React; зафиксировать в ADR.

**Выход:** `pytest` зелёный, `/account` отдаёт заглушку, миграции применяются, фейковый IdP работает.

**Оценка:** 8 pd.

---

### Sprint 1 — авторизация и привязка устройства (2 недели)

**Задачи.**

- [ ] `account/auth.py`: `GET /account/auth/google/start` (state, nonce, PKCE S256, redirect), `GET /account/auth/google/callback`.
- [ ] Проверка ID token: подпись по JWKS (кэш ключей), `iss`, `aud`, `exp`, `iat`, `nonce`, `email_verified`.
- [ ] Upsert аккаунта по `sub`; создание личного тенанта; запись `last_login_at`.
- [ ] Сессии: случайный `session_id` в БД (хранится `sha256`), подписанная cookie (`HttpOnly`, `Secure`, `SameSite=Lax`), idle 7 дней / absolute 30 дней, ротация при входе, выход.
- [ ] CSRF: double-submit токен для всех POST; проверка Origin/Referer.
- [ ] Страницы: `/account/login`, `/account/` (пустой дашборд), `/account/logout`, ошибки.
- [ ] Привязка устройства (канонический flow): `POST /v1/devices/pair` принимается **только с действующим device token**, создаёт `code` (8 символов), `pair_secret` (32 B, знает только устройство), `fingerprint` и TTL 10 минут; ключи в ответе не выдаются. `GET /v1/devices/pair/{code}` требует `pair_secret` и отдаёт только статус (`pending/claimed/expired`). `POST /account/api/claim` (сессия + CSRF) показывает имя узла, ОС, fingerprint и привязывает устройство к тенанту; device token и link key не перевыпускаются. Старый admin-`approve` остаётся только для `MESH_REGISTER_MODE=pair` (первичная выдача ключей новому узлу) и помечается legacy. Никакого `pair_url` без маршрута: либо страница `/pair?code=`, либо поле не отдаётся.
- [ ] Наследие: устройство, зарегистрированное до v2, привязывается тем же кодом; `registry[user]["device_id"]`, `["tenant_id"]` проставляются после claim.
- [ ] Проверка владельца: claim только для залогиненного; чужой код/повтор → 400 без деталей.
- [ ] Аудит: `login`, `logout`, `device_claim`, `claim_failed`.

**API.**

| Метод | Путь | Назначение |
| :--- | :--- | :--- |
| `GET` | `/account/auth/google/start?next=` | редирект на Google, защита `next` от open redirect |
| `GET` | `/account/auth/google/callback` | обмен code, проверка ID token, сессия |
| `POST` | `/account/logout` | удаление сессии + cookie |
| `POST` | `/account/api/claim` | `{code}` → привязка устройства к тенанту |
| `GET` | `/account/api/session` | текущий пользователь/тенант (для htmx) |

**Тесты.**

- state/nonce: подмена, повтор, отсутствие → отказ.
- ID token: просроченный, чужой `aud`, неверная подпись, `email_verified=false` → отказ.
- cookie: без подписи, просроченная, после logout → отказ.
- CSRF: POST без токена/с чужим Origin → 403.
- open redirect: `next=//evil`, `https://evil` → только локальный путь.
- claim: перебор кода (rate limit), повторное использование, чужой аккаунт.
- сессия: фиксация (новый id после логина), параллельные сессии.

**Приёмка.**

- Пользователь входит через Google, видит пустой кабинет, выходит; после перезагрузки сессия сохраняется.
- Устройство, показывающее код, появляется в кабинете после ввода кода.
- Без `MESH_ACCOUNT=1` поведение шлюза не меняется.

**Оценка:** 26 pd.

---

### Sprint 2 — кабинет, агенты, журнал (2 недели)

**Задачи.**

**Агенты и ключи.**

- [ ] При первом `initialize` шлюз создаёт `AgentConnection` из `clientInfo.name`; если ключ уже привязан к агенту — обновляет `last_used`.
- [ ] `POST /account/api/devices/{id}/agents` — создать агента вручную и выпустить отдельный link-key; URL показывается один раз, в БД — только хеш. `GET .../agents` — список (без ключей).
- [ ] Отдельные link-key на агента: `registry[user]["agents"]`, хеш ключа, `revoked_at`; существующий `link_key` становится агентом `default` (миграция без разрыва ссылок).
- [ ] Проверка токена: сначала agent-key, затем legacy `link_key`/device token; резолв в `agent_id`.
- [ ] `PATCH /account/api/agents/{id}`: `label`, `mode`, `scopes`, `workspace`; `DELETE` — отзыв.
- [ ] Отзыв: ключ удаляется/помечается, активные сессии агента закрываются, следующие вызовы → 401.

**Кабинет.**

- [ ] `/account/` — дашборд: устройства онлайн/офлайн, агенты, вызовы за сутки, алерты.
- [ ] `/account/devices` — таблица: имя, ОС, версия, статус, последний контакт, агенты, действия.
- [ ] `/account/devices/{id}` — карточка: переименование, отзыв, агенты, статистика, опасная зона.
- [ ] `/account/agents` — список: клиент, устройство, режим, scopes, последняя активность, отзыв.
- [ ] `/account/usage` — фильтры по устройству/агенту/периоду, график по дням, топ инструментов, ошибки.
- [ ] `/account/settings` — профиль, «делиться статистикой», удаление аккаунта.
- [ ] i18n ru/en, пустые состояния, ошибки, адаптивность, доступность (фокус, aria).

**Локальный журнал.**

- [ ] `core/usage.py`: JSONL `~/.config/remote-harness-mcp/usage.jsonl` (Linux: `/etc/...` для службы), 0600.
- [ ] Поля: `ts`, `call_id`, `agent_id`, `agent_label`, `tool`, `mode`, `status` (`ok|error|denied|cancelled`), `duration_ms`, `bytes`, `error_code`.
- [ ] Ротация: 10 МБ × 5, старые gzip; ретенция 90 дней; `DELETE /v1/usage` — очистка.
- [ ] `GET /v1/usage?since=&limit=&tool=&status=`, `GET /v1/usage/summary?days=7`, `GET /v1/usage/export.csv`.
- [ ] CLI: `remote-harness-mcp usage [--since 7d] [--json] [--export file.csv] [--clear]`.
- [ ] Отправка агрегатов: раз в час `POST /account/api/usage/ingest` с суточными счётчиками (tool, ok/err, duration_sum, bytes_sum), без аргументов; флаг «делиться статистикой» (по умолчанию включён для своего аккаунта).
- [ ] В журнал не попадают аргументы, пути, вывод, токены; тест-сканер на секреты.

**Тесты.**

- ротация и gzip; чтение после ротации; повреждённая строка не ломает журнал.
- отсутствие payload: вызов `bash_exec("cat secret")` не оставляет `cat secret` в файле.
- агрегаты: сумма совпадает с событиями; идемпотентность ingest (`device_id + day + tool + agent`).
- права: чужой device/agent → 404; отзыв агента → 401.
- кабинет: XSS в имени устройства, SQL-инъекция в фильтрах, IDOR.

**Приёмка.**

- В кабинете видны устройства и агенты; переименование и отзыв работают.
- Журнал на устройстве содержит вызовы, экспортируется в CSV, очищается.
- Статистика в кабинете совпадает с локальной сводкой (±1%).

**Оценка:** 38 pd.

---

### Sprint 3 — трей и меню настроек (2 недели)

**Задачи.**

**Трей (Windows first, Avalonia).**

- [ ] `desktop/RhmTray`: single-file publish `win-x64`, иконка в трее, меню, локализация ru/en.
- [ ] Состояния иконки: `offline`, `idle`, `active`, `unclaimed`, `agent_down`, `paused`, `update_required`, `error`; `approval` — только Wave 2 (пульсация — в Wave 2; в Wave 1 — цвет/точка).
- [ ] Меню:

```
Remote Harness MCP — DESKTOP-01
──────────────────────────────
● Подключён · 2 агента · 14:32
Режим по умолчанию:  ( ) Read-only  (•) Agent
Рабочая папка…            C:\work
Права агентов…            (в кабинет)
Журнал использования…
──────────────────────────────
Автозапуск             [вкл]
Активность             [вкл]
Уведомления            [вкл]
Пульсация              [вкл]
──────────────────────────────
Открыть кабинет
Переподключить
О программе
Выход
```

- [ ] Окно журнала: таблица (время, агент, инструмент, статус, длительность), фильтры, экспорт CSV, очистка.
- [ ] Автозапуск: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (без админа), проверка фактического состояния, снятие.
- [ ] Пауза: `POST /v1/pause` → узел отвечает `node_paused` на новые вызовы, туннель жив.
- [ ] Уведомления: тост при подключении агента и при ошибке авторизации.
- [ ] «Открыть кабинет»: `MESH_ACCOUNT_BASE_URL/account`.

**Control API.**

- [ ] `core/control.py`: loopback HTTP + SSE, токен в `control.token` (0600), отказ без токена/не с localhost.
- [ ] `GET /v1/state`, `GET /v1/events`, `PATCH /v1/settings` (`default_mode`, `workspace`, `paused`, `autostart`, `pulse`), `GET /v1/usage*`.
- [ ] Связь с агентом: состояние туннеля, версия, активные вызовы (пока без текста запроса).

**Режим и права (тонкий слой E3).**

- [ ] `SessionContext(mode, scopes, workspace)` в `mcp_tools`; глобальный `MESH_READ_ONLY` — старший предохранитель.
- [ ] Шлюз передаёт `ctx` в `tools/call`; узел применяет; `tools/list` фильтруется по scopes.
- [ ] Режим по умолчанию для новых агентов: `read_only`; смена в кабинете применяется к новым вызовам.
- [ ] Рабочая папка: per-agent из кабинета; в трее — папка по умолчанию для новых агентов.
- [ ] Проверка: read-only агент не может вызвать `bash_exec`/`write_file`/`share_file` ни через мост, ни напрямую.

**Тесты.**

- control API: без токена, с чужим токеном, с не-localhost → отказ; SSE переживает реконнект.
- автозапуск: после перезагрузки (ручной чек) трей и узел поднимаются.
- пауза: вызов → `node_paused`, снятие паузы → вызов проходит.
- режим: матрица «инструмент × режим × слой».
- права: отзыв агента в кабинете → его следующий вызов 401, другие агенты работают.

**Приёмка.**

- Все пункты меню работают и меняют реальное поведение (не только флаг в UI).
- Журнал открывается из трея и совпадает с `remote-harness-mcp usage`.
- Read-only нельзя обойти.
- Трей не требует админ-прав; после перезагрузки работает.

**Оценка:** 40 pd.

---

## 5. Модель данных (SQLite)

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE accounts (
  id TEXT PRIMARY KEY,
  google_iss TEXT NOT NULL,
  google_sub TEXT NOT NULL,
  aud TEXT NOT NULL,
  email TEXT NOT NULL,
  email_verified INTEGER NOT NULL DEFAULT 0,
  name TEXT,
  created_at TEXT NOT NULL,
  last_login_at TEXT,
  share_usage INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  UNIQUE (google_iss, google_sub)
);

-- Browser login sessions. The MCP session lives in mcp_sessions (Wave 2).
CREATE TABLE account_sessions (
  id_hash TEXT PRIMARY KEY,            -- sha256(cookie value), не сам идентификатор
  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  idle_expires_at TEXT NOT NULL,       -- 7 дней
  absolute_expires_at TEXT NOT NULL,   -- 30 дней
  last_seen_at TEXT,
  revoked_at TEXT,
  rotated_from TEXT,
  step_up_at TEXT,                     -- fresh-auth для claim/revoke/delete
  ip_hash TEXT,
  ua_hash TEXT
);
CREATE INDEX idx_account_sessions_account ON account_sessions(account_id);

CREATE TABLE tenants (
  id TEXT PRIMARY KEY,
  owner_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  plan TEXT NOT NULL DEFAULT 'personal',
  data_region TEXT,
  bridge_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE tenant_members (
  tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'owner',
  created_at TEXT NOT NULL,
  PRIMARY KEY (tenant_id, account_id)
);

CREATE TABLE devices (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  node_name TEXT UNIQUE NOT NULL,      -- ключ в registry.json
  display_name TEXT,
  platform TEXT,
  arch TEXT,
  version TEXT,
  fingerprint TEXT,                    -- отпечаток ключа/устройства
  pubkey TEXT,
  mode_default TEXT NOT NULL DEFAULT 'read_only',
  workspace TEXT,
  claimed_at TEXT,
  last_seen_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE agents (
  id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  client_type TEXT,
  key_sha256 TEXT UNIQUE,              -- ak_..., только хеш
  key_prefix TEXT,                     -- первые 8 символов для UI
  mode TEXT NOT NULL DEFAULT 'read_only',
  scopes TEXT NOT NULL DEFAULT '["files:read"]',
  workspace TEXT,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT,
  revoked_reason TEXT
);
CREATE INDEX idx_agents_device ON agents(device_id, revoked_at);

CREATE TABLE grants (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  scopes TEXT NOT NULL,
  mode TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  revoked_at TEXT
);

CREATE TABLE usage_daily (
  tenant_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  day TEXT NOT NULL,                   -- UTC YYYY-MM-DD
  tool TEXT NOT NULL,
  ok INTEGER NOT NULL DEFAULT 0,
  err INTEGER NOT NULL DEFAULT 0,
  denied INTEGER NOT NULL DEFAULT 0,
  denied_at_bridge INTEGER NOT NULL DEFAULT 0,
  lost INTEGER NOT NULL DEFAULT 0,     -- узел не получил ответ
  duration_ms_sum INTEGER NOT NULL DEFAULT 0,
  duration_ms_max INTEGER NOT NULL DEFAULT 0,
  bytes_sum INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (tenant_id, device_id, agent_id, day, tool)
);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  tenant_id TEXT,
  account_id TEXT,
  actor TEXT,
  action TEXT NOT NULL,
  target TEXT,
  request_id TEXT,
  ts TEXT NOT NULL,
  meta_json TEXT,
  prev_hash TEXT,
  hash TEXT
);
CREATE INDEX idx_audit_tenant_ts ON audit_events(tenant_id, ts);

CREATE TABLE device_claims (
  code TEXT PRIMARY KEY,
  pair_secret_hash TEXT NOT NULL,      -- знает только устройство
  node_name TEXT NOT NULL,
  account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
  tenant_id TEXT REFERENCES tenants(id) ON DELETE SET NULL,
  fingerprint TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
```

Миграции — пронумерованные `.sql` в `account/migrations/`, применяются при старте в транзакции; таблица `schema_version`. Без ORM, доступ через `sqlite3` и тонкий слой репозиториев — позже заменяется на Postgres без изменения маршрутов.

**Связь с `registry.json`:** поле `device_id`/`tenant_id` пишется в запись реестра после claim; при старте — обратная сверка (если в реестре есть `device_id`, а в БД нет — запись помечается `orphaned` и показывается в кабинете как «требует восстановления»).

---

## 6. Локальный журнал

### Формат

```json
{"ts":"2026-11-11T14:32:05.123Z","call_id":"01J...","agent_id":"ag_7f...","agent_label":"Claude","tool":"bash_exec","mode":"agent","status":"ok","duration_ms":842,"bytes":1204,"error_code":null}
```

Правила:

- **Нет** аргументов, путей, вывода, заголовков, токенов. Только метаданные.
- `bytes` — суммарный размер ответа, берётся **с узла** (не с моста: мост обрезает вывод).
- `status` ∈ `ok|error|denied|cancelled|lost`; `error_code` — стабильный код (`approval_denied`, `mode_denied`, `timeout`, `cancelled`, `spawn_error`).
- `call_id` — ключ идемпотентности; повторный `tools/call` с тем же id не создаёт вторую запись.
- Запись — атомарная строка (append + flush); при сбое питания допускается потеря последней строки, чтение её игнорирует.
- Ротация по размеру; старые файлы `usage-YYYYMMDD.jsonl.gz`.
- Ретенция 90 дней по умолчанию (`MESH_USAGE_RETENTION_DAYS`).
- Файл 0600; на Windows — ACL только для пользователя; путь — только через `core/paths.config_dir()`/`MESH_STATE_DIR`.

### Сводка

`usage summary` считает: вызовы, успех/ошибка/отказ, p50/p95, байты, топ инструментов, по агентам и дням. Считается потоково, без загрузки всего файла в память (или с ограничением 100 МБ).

### Отправка в кабинет

- По умолчанию — **включена** для собственного аккаунта (это его же данные), но с явным тумблером и объяснением.
- Отправляются только суточные агрегаты; `call_id`, точное время и аргументы не уходят.
- Аутентификация — device token; `device_id` и `tenant_id` берутся **из аутентификации**, а не из тела.
- Идемпотентность: ключ `(tenant_id, device_id, agent_id, day, tool)`; повторная отправка **не перезаписывает** счётчики, а применяет `max()` (защита от отката и replay).
- При отсутствии сети — очередь в `usage-outbox.jsonl`, отправка при следующем подключении; лимит очереди и TTL.

---

## 7. Меню настроек

### Дерево

| Пункт | Что делает | Где хранится |
| :--- | :--- | :--- |
| Статус | шлюз, агенты, последний контакт | state |
| Режим по умолчанию | `read_only`/`agent` для новых агентов | `agent.env` + кабинет |
| Рабочая папка | папка по умолчанию для новых агентов | `agent.env` |
| Права агентов | открывает кабинет на странице прав | — |
| Журнал использования | окно с таблицей и экспортом | `usage.jsonl` |
| Автозапуск | регистрация/снятие | ОС |
| Активность | пауза приёма вызовов | control |
| Уведомления | тосты | настройки трея |
| Пульсация | анимация (в Wave 2 — активная) | настройки трея |
| Открыть кабинет | браузер | — |
| Переподключить | рестарт туннеля | agent |
| О программе | версия, обновление, логи | — |
| Выход | остановить трей (узел продолжает службой) | — |

### Поведение

- Переключение режима по умолчанию требует подтверждения: «Новые агенты получат право менять файлы и выполнять команды».
- Пауза не рвёт туннель: агент видит `node_paused` с понятным текстом.
- Автозапуск отражает фактическое состояние (проверка записи в реестре/plist/autostart), а не только галочку.
- Ошибки показываются человеческим текстом + код; «Подробнее» ведёт в лог.

### Платформы

| ОС | Автозапуск | Трей | Статус Wave 1 |
| :--- | :--- | :--- | :--- |
| Windows 10/11 | `HKCU\...\Run` | Avalonia TrayIcon | ✅ |
| macOS 14/15 | LaunchAgent | Avalonia TrayIcon | ⏳ Wave 2 |
| Linux (GNOME/KDE) | `systemd --user` + autostart | StatusNotifier/AppIndicator | ⏳ Wave 2 |

---

## 8. Кабинет: экраны и состояния

| Экран | Содержимое | Пустое состояние |
| :--- | :--- | :--- |
| `/account/` | карточки: устройства онлайн, агенты, вызовы за 24 ч, алерты | «Привяжите первое устройство» + код/инструкция |
| `/account/devices` | таблица, поиск, фильтр по статусу | «Нет устройств» |
| `/account/devices/{id}` | имя, ОС, версия, статус, агенты, usage, опасная зона | — |
| `/account/agents` | список, режим, scopes, последняя активность, отзыв | «Нет подключённых агентов» |
| `/account/usage` | период, устройство, агент, график, топ инструментов, ошибки | «Пока нет данных» |
| `/account/settings` | профиль, статистика, удаление аккаунта | — |

**Принципы.**

- Опасные действия (отзыв, удаление) — с подтверждением и вводом имени устройства.
- Все списки — с сортировкой и пагинацией (cursor), без «прыжков» при обновлении.
- Никаких payload в интерфейсе: только метаданные.
- i18n: ru/en, строки в JSON, дата/время в локали.
- Доступность: клавиатура, контраст, `aria-live` для ошибок.
- Мобильный web — читаемый, но управление с телефона — Wave 2/3.

---

## 9. Безопасность

| Угроза | Мера |
| :--- | :--- |
| Кража сессии | HttpOnly/Secure/SameSite, TTL, ротация id, отзыв всех сессий |
| CSRF | double-submit + Origin/Referer, SameSite=Lax |
| Open redirect | `next` — только относительный путь из allowlist |
| Подделка ID token | подпись JWKS, `iss/aud/exp/nonce`, `email_verified` |
| Перебор кода привязки | 8 символов, TTL 10 мин, rate limit по IP и аккаунту, одноразовость |
| IDOR | каждый запрос проверяет `tenant_id`/`account_id`; чужая сущность — 404 |
| XSS | автоэкранирование Jinja, CSP, запрет inline-скриптов |
| SQL-инъекции | параметризованные запросы, никакой конкатенации |
| Утечка ключей | link-key хранится хешем; показывается один раз; отзыв мгновенный |
| Утечка журнала | 0600, нет payload, редактирование, очистка |
| Подмена устройства | claim только по коду с экрана устройства; отпечаток в кабинете |
| Брутфорс логина | rate limit на callback, логирование, блокировка после N ошибок |

**Секреты.** `MESH_SESSION_SECRET` — 32+ случайных байта, генерируется установщиком, 0600. `MESH_GOOGLE_CLIENT_SECRET` — только на сервере. Cookie `__Host-rhm_session` (без Domain, Path=/).

**Приватность.** В БД кабинета нет содержимого команд и файлов; только метаданные и суточные агрегаты. Страница `/account/privacy` объясняет, что видит сервер.

---

## 10. Тесты и приёмка

### Автоматические

| Набор | Что покрывает |
| :--- | :--- |
| `tests/test_account_auth.py` | OIDC, state/nonce, ID token, сессии, CSRF, open redirect |
| `tests/test_device_claim.py` | claim, TTL, перебор, чужой аккаунт, наследие |
| `tests/test_account_api.py` | IDOR, rename, revoke, пагинация, i18n-строки |
| `tests/test_agents.py` | создание из initialize, ключи, режим, scopes, отзыв |
| `tests/test_usage.py` | формат, ротация, редактирование, сводка, экспорт, очистка |
| `tests/test_usage_ingest.py` | агрегаты, идемпотентность, outbox |
| `tests/test_control_api.py` | токен, localhost, SSE, settings |
| `tests/test_session_context.py` | mode × tool × layer, глобальный предохранитель |

### Ручной сценарий приёмки (30 минут)

1. Чистая машина: установить узел, открыть трей, увидеть «не привязан».
2. Войти в кабинет через Google, ввести код с устройства → устройство появилось.
3. Подключить Gemini по ссылке → агент появился в кабинете.
4. Переключить агента в `Agent`, выполнить `ls` → успех; переключить в `Read-only` → отказ.
5. Переименовать устройство, отозвать агента → его вызовы падают, другие работают.
6. Выполнить 3 команды, открыть журнал в трее и `usage --json` → данные совпадают.
7. Экспортировать CSV, очистить журнал.
8. Включить/выключить автозапуск, перезагрузиться → состояние совпадает.
9. Выйти из аккаунта → кабинет недоступен, устройство остаётся на связи.
10. Проверить, что в `usage.jsonl` нет текста команд.

### Definition of Done

- [ ] Тесты зелёные, покрытие нового кода ≥ 80%.
- [ ] Документация: README_RU/EN (раздел «Кабинет»), docs/ACCOUNT.md.
- [ ] Фича-флаг и graceful degradation без зависимостей.
- [ ] Security-чеклист пройден, секреты не в логах.
- [ ] Демо-запись и скриншоты.
- [ ] Обратная совместимость: старые узлы и ссылки работают.

---

## 11. Оценка и календарь

| Спринт | Недели | pd | Кто |
| :--- | :---: | ---: | :--- |
| Sprint 0 | 1 | 8 | Backend + DevOps |
| Sprint 1 | 2–3 | 26 | Backend (auth) + Frontend |
| Sprint 2 | 4–5 | 38 | Backend (2) + Frontend |
| Sprint 3 | 6–7 | 40 | Desktop + Backend |
| QA/security/доки | 1–7 | 12 | QA + Security |
| **Ревью (task + final)** | 1–7 | **30–45** | независимые ревьюеры |
| **Итого** | **7 + 1 неделя стабилизации** | **154–169** | 5–5,5 FTE |

При команде 5,5 FTE и 8 неделях ёмкость ≈ 220 pd; буфер покрывает ревью, стабилизацию и 15% риска. Без бюджета ревью план не соответствует Superpowers: ревью — часть работы, а не бесплатное приложение.

**Критический путь:** OIDC и сессии → claim → агенты/ключи → контекст режима → трей. Журнал можно вести параллельно с Sprint 1 (он не зависит от аккаунта), но UI журнала — после control API.

---

## 12. Риски

| Риск | Вер. | Влияние | Мера |
| :--- | :---: | :---: | :--- |
| Нет Google OAuth-клиента/домена | С | В | Dev-режим на localhost; брокер позже; не блокирует журнал/трей |
| Cookie/сессии на чужом домене | Н | В | `__Host-`, Secure, точный `MESH_ACCOUNT_BASE_URL` |
| Наследие: устройства без аккаунта | В | С | claim-код, «сироты» в кабинете, миграционный скрипт |
| Рост `gateway.py` | В | С | Всё в `account/`, только mount и хуки; ревью границ |
| Утечка статистики | С | С | Только агрегаты, тумблер, privacy-страница |
| Трей на Windows без подписи | В | С | Self-signed/подпись позже, понятное предупреждение |
| Per-agent ключи ломают старые ссылки | С | В | Legacy `link_key` = агент `default`, без разрыва |
| Объём: ещё один «MVP» | С | В | Жёсткие границы §2, перенос OAuth-клиентов в Wave 2 |

---

## 13. Открытые вопросы

| # | Вопрос | Рекомендация |
| :--- | :--- | :--- |
| 1 | Jinja+htmx или React для кабинета | Jinja+htmx: быстрее, меньше сборки; React — для графиков позже |
| 2 | Где хостить кабинет | Тот же хост, что шлюз, путь `/account`; выделение в Edge — Wave 3 |
| 3 | Google-клиент | Один вендорный на этапе Wave 1; self-host — позже |
| 4 | Делиться статистикой по умолчанию | Вкл для своего аккаунта, явный тумблер, без payload |
| 5 | Per-agent ключи сейчас | Да, иначе «отключить агента» небезопасно |
| 6 | Трей: Windows-only в Wave 1 | Да, Avalonia-код сразу кросс-платформенный, релизы macOS/Linux — Wave 2 |

---

## 14. Что дальше (Wave 2)

- OAuth 2.1 для MCP-клиентов (Claude/ChatGPT), consent, DCR, токены.
- Hover-панель с текстом запроса и abort (E2).
- Per-agent переключатель режима в трее, список активных вызовов.
- Полные approvals (allow once/always, TTL, причины).
- Онбординг «выбор облачного агента» (E7) — использует уже готовые кабинет и агентов.
- macOS/Linux трей.

Это следующий логичный блок: после Wave 1 у нас есть аккаунт, устройство, агент и права — на них OAuth и онбординг ложатся без переделки модели данных.

---

## Приложение A. Последовательность claim

```
Устройство                         Шлюз/аккаунт                     Браузер
   │ POST /v1/devices/pair            │                               │
   │  Authorization: device token     │                               │
   │─────────────────────────────────▶│ pending_pairs[code]           │
   │◀─ {code, pair_secret,           ─│  fingerprint, expires         │
   │     fingerprint, expires}        │                               │
   │ показывает код в трее             │                               │
   │ GET /pair/{code} + pair_secret    │                               │
   │─────────────────────────────────▶│ статус pending/claimed        │
   │                                   │◀── POST /account/api/claim ───│
   │                                   │    {code} (сессия+CSRF)       │
   │                                   │ показывает имя/ОС/fingerprint │
   │                                   │ registry[node].device_id=...  │
   │◀── статус claimed (поллинг) ──────│ tenant_id, device_id           │
   │ локально сохраняет привязку        │                               │
```

## Приложение B. Конфиг

```dotenv
MESH_ACCOUNT=1
MESH_ACCOUNT_DB=/var/lib/rhm/account.db
MESH_ACCOUNT_BASE_URL=https://<домен>
MESH_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
MESH_GOOGLE_CLIENT_SECRET=...
MESH_SESSION_SECRET=<32+ random bytes>
MESH_AUTH_MODE=google
MESH_USAGE_RETENTION_DAYS=90
MESH_USAGE_SHARE=1
```

## Приложение C. Права (scopes) Wave 1

| Группа | Инструменты |
| :--- | :--- |
| `files:read` | `list_dir`, `read_file`, `grep_search`, `glob_find` |
| `files:write` | `write_file`, `edit_file` |
| `shell:exec` | `bash_exec`, `run_job`, `job_kill` |
| `device:control` | `device_control`, `device_capture`, `device_messages` |
| `shares:manage` | `share_file`, `serve_dir`, `unshare` |
| `system:update` | `mesh_update` |

`read_only` = только `files:read` + информационные инструменты (`system_info`, `system_vitals`, `mesh_status`, `device_info`, `share_list`). `agent` = выбранные группы. Глобальный `MESH_READ_ONLY=1` отключает всё мутирующее независимо от scopes.
